with events as (
    select * from {{ source('loyalty', 'events') }}
)

, typed as (
    select
        event_id
        , member_id
        , event_type
        , cast(event_at as timestamp) as event_at
        , transaction_id
        , entitlement_id
        , cast(points as integer) as points
        , _load_id as source_load_id
    from events
)

-- deduplicate: the export is at least once, so an event can arrive twice; events never change, so keep the first.
select *
from typed
qualify row_number() over (partition by event_id order by source_load_id) = 1
