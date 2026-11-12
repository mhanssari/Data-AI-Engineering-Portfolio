-- Invariant 10: a rate exists only where its window has closed, and is never 0 in place of "not yet known".
-- Repeat purchase: an immature cohort has a null rate; a mature one has a rate and every member's window closed.
-- Retention: no cell for a month that has not ended.
with as_of as (
    select max(date_day) as as_of_date from {{ ref('dim_date') }} where not is_after_as_of_date
)

, repeat_cohorts as (
    select * from {{ ref('agg_repeat_purchase_cohort') }}
)

, retention_cells as (
    select * from {{ ref('agg_retention_cohort') }}
)

select
    'repeat purchase' as metric
    , cohort_month_start_date
    , cast(null as integer) as months_since_enrolment
from repeat_cohorts
cross join as_of
where (not is_mature and repeat_purchase_rate is not null)
    or (is_mature and (repeat_purchase_rate is null or window_closed_member_count <> cohort_member_count))
    or (is_mature <> ({{ dbt.dateadd("day", 60, dbt.last_day("cohort_month_start_date", "month")) }} <= as_of.as_of_date))

union all

select
    'retention'
    , cohort_month_start_date
    , months_since_enrolment
from retention_cells
cross join as_of
where {{ dbt.last_day("measured_month_start_date", "month") }} > as_of.as_of_date
