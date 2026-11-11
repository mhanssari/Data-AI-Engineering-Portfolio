/*
    One row per enrolment month and months since enrolment: the share of the cohort active at the end of that
    month. A cell exists only once its month has closed (invariant 10). Offsets 0 to 2 measure early activity,
    not retention, because the activity window is 90 days; the report starts the curve at offset 3.
*/
with member_months as (
    select * from {{ ref('fct_member_month') }}
)

, cells as (
    select
        enrolment_month_start_date as cohort_month_start_date
        , months_since_enrolment
        , month_start_date as measured_month_start_date
        , cast(count(*) as integer) as cohort_member_count
        , cast(sum(case when is_active then 1 else 0 end) as integer) as active_member_count
    from member_months
    where is_complete_month
    group by enrolment_month_start_date, months_since_enrolment, month_start_date
)

select
    cohort_month_start_date
    , months_since_enrolment
    , measured_month_start_date
    , cohort_member_count
    , active_member_count
    , cast(active_member_count * 1.0 / cohort_member_count as decimal(9, 4)) as retention_rate
    , months_since_enrolment < 3 as is_early_activity
from cells
