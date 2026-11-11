-- Invariant 7: fct_member_month holds exactly one row per member for every month from the enrolment month to the
-- as-of month, whether or not the member bought anything. Returns members with missing or extra months.
with members as (
    select member_id, enrolment_month_start_date from {{ ref('dim_member') }}
)

, as_of as (
    select max(date_day) as as_of_date from {{ ref('dim_date') }} where not is_after_as_of_date
)

, expected as (
    select
        members.member_id
        , {{ dbt.datediff("members.enrolment_month_start_date", "as_of.as_of_date", "month") }} + 1 as expected_month_count
    from members
    cross join as_of
)

, actual as (
    select
        member_id
        , count(*) as actual_month_count
        , count(distinct month_start_date) as distinct_month_count
        , min(month_start_date) as first_month_start_date
    from {{ ref('fct_member_month') }}
    group by member_id
)

select
    expected.member_id
    , expected.expected_month_count
    , actual.actual_month_count
from expected
left join actual on expected.member_id = actual.member_id
left join members on expected.member_id = members.member_id
where actual.member_id is null
    or actual.actual_month_count <> expected.expected_month_count
    or actual.distinct_month_count <> actual.actual_month_count
    or actual.first_month_start_date <> members.enrolment_month_start_date
