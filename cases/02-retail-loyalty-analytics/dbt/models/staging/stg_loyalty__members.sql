with members as (
    select * from {{ source('loyalty', 'members') }}
)

, typed as (
    select
        member_id
        , cast(enrolled_at as timestamp) as enrolled_at
        , cast(cast(enrolled_at as timestamp) as date) as enrolled_date
        , home_store_id
        , enrolment_channel
        , cast(birth_month as integer) as birth_month
        , cast(updated_at as timestamp) as updated_at
        , _load_id as source_load_id
    from members
)

-- deduplicate: a correction re-sends the member with a later updated_at, and the corrected value wins.
select *
from typed
qualify row_number() over (partition by member_id order by updated_at desc, source_load_id desc) = 1
