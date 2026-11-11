/*
    One row per member per calendar month, from the enrolment month to the as-of month: a dense periodic snapshot.
    Dense on purpose: inactivity is about months in which nothing happened, so a member who never bought still has
    a row every month (invariant 7, planted D8).

    Each month is measured on its last day, or on the as-of date for the month in progress:
      is_active    at least one qualifying purchase in the 90 days ending on the measurement date
      is_eligible  enrolled at least 90 days before the measurement date, so the whole window is observable
*/
with members as (
    select member_id, enrolled_date, enrolment_month_start_date from {{ ref('dim_member') }}
)

, purchases as (
    select member_id, purchase_date from {{ ref('int_member_purchases') }}
)

, sales_lines as (
    select member_id, transaction_date, net_amount from {{ ref('fct_sales_line') }} where member_id is not null
)

, as_of as (
    select as_of_date from {{ ref('int_as_of_date') }}
)

, months as (
    select distinct month_start_date
    from {{ ref('dim_date') }}
    cross join as_of
    where month_start_date <= as_of.as_of_date
)

, member_months as (
    select
        members.member_id
        , members.enrolled_date
        , members.enrolment_month_start_date
        , months.month_start_date
        , cast({{ dbt.last_day("months.month_start_date", "month") }} as date) as month_end_date
        , as_of.as_of_date
    from members
    inner join months on months.month_start_date >= members.enrolment_month_start_date
    cross join as_of
)

, measured as (
    select
        *
        , case when month_end_date <= as_of_date then month_end_date else as_of_date end as measurement_date
    from member_months
)

, activity as (
    select
        measured.member_id
        , measured.month_start_date
        , count(purchases.purchase_date) as purchases_in_window_count
        , count(case when purchases.purchase_date >= measured.month_start_date then 1 end) as purchases_in_month_count
    from measured
    left join purchases
        on measured.member_id = purchases.member_id
        and purchases.purchase_date > {{ dbt.dateadd("day", -90, "measured.measurement_date") }}
        and purchases.purchase_date <= measured.measurement_date
    group by measured.member_id, measured.month_start_date
)

, spend as (
    select
        member_id
        , cast({{ dbt.date_trunc("month", "transaction_date") }} as date) as month_start_date
        , sum(net_amount) as net_sales_amount
    from sales_lines
    group by 1, 2
)

select
    measured.member_id
    , measured.month_start_date
    , measured.measurement_date
    , measured.enrolment_month_start_date
    , cast({{ dbt.datediff("measured.enrolment_month_start_date", "measured.month_start_date", "month") }} as integer)
        as months_since_enrolment
    , measured.month_end_date <= measured.as_of_date as is_complete_month
    , {{ dbt.dateadd("day", 90, "measured.enrolled_date") }} <= measured.measurement_date as is_eligible
    , activity.purchases_in_window_count > 0 as is_active
    , cast(activity.purchases_in_month_count as integer) as qualifying_purchase_count
    , cast(coalesce(spend.net_sales_amount, 0) as decimal(18, 2)) as net_sales_amount
from measured
inner join activity
    on measured.member_id = activity.member_id
    and measured.month_start_date = activity.month_start_date
left join spend
    on measured.member_id = spend.member_id
    and measured.month_start_date = spend.month_start_date
