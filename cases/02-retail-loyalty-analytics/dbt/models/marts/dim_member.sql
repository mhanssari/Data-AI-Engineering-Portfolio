-- One row per member with current attributes (type 1): a corrected enrolment date replaces the original
-- everywhere, so cohorts use the corrected value. What was reported before is in snp_member.
with members as (
    select * from {{ ref('stg_loyalty__members') }}
)

select
    member_id
    , enrolled_at
    , enrolled_date
    , cast({{ dbt.date_trunc("month", "enrolled_date") }} as date) as enrolment_month_start_date
    , home_store_id
    , enrolment_channel
    , birth_month
from members
