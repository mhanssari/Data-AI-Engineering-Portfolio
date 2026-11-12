/*
    One row per first-purchase month: how many of the members whose first qualifying purchase fell in that month
    bought again on a later date within 60 days.

    A cohort is mature when the last member's window has closed: the cohort month's last day plus 60 days is on
    or before the as-of date. An immature cohort has a null rate and is_mature = false, never 0%: its members have
    not had their chance yet (invariant 10, planted D15).
*/
with purchases as (
    select member_id, purchase_date, purchase_number from {{ ref('int_member_purchases') }}
)

, as_of as (
    select as_of_date from {{ ref('int_as_of_date') }}
)

, first_purchases as (
    select
        member_id
        , purchase_date as first_purchase_date
        , {{ dbt.dateadd("day", 60, "purchase_date") }} as window_closes_date
    from purchases
    where purchase_number = 1
)

, repeats as (
    select first_purchases.member_id
    from first_purchases
    inner join purchases
        on first_purchases.member_id = purchases.member_id
        and purchases.purchase_date > first_purchases.first_purchase_date
        and purchases.purchase_date <= first_purchases.window_closes_date
    group by first_purchases.member_id
)

, cohort_members as (
    select
        cast({{ dbt.date_trunc("month", "first_purchases.first_purchase_date") }} as date) as cohort_month_start_date
        , first_purchases.window_closes_date <= as_of.as_of_date as is_window_closed
        , repeats.member_id is not null as has_repeat
        , as_of.as_of_date
    from first_purchases
    cross join as_of
    left join repeats on first_purchases.member_id = repeats.member_id
)

, cohorts as (
    select
        cohort_month_start_date
        , cast(count(*) as integer) as cohort_member_count
        , cast(sum(case when is_window_closed then 1 else 0 end) as integer) as window_closed_member_count
        , cast(sum(case when is_window_closed and has_repeat then 1 else 0 end) as integer) as repeat_member_count
        , {{ dbt.dateadd("day", 60, dbt.last_day("cohort_month_start_date", "month")) }} <= max(as_of_date) as is_mature
    from cohort_members
    group by cohort_month_start_date
)

select
    cohort_month_start_date
    , cohort_member_count
    , window_closed_member_count
    , repeat_member_count
    , is_mature
    , case
        when is_mature then cast(repeat_member_count * 1.0 / cohort_member_count as decimal(9, 4))
    end as repeat_purchase_rate
from cohorts
