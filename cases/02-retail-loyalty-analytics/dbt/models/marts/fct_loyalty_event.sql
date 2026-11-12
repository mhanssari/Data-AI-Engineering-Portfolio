-- One row per distinct programme event. Points but never money: revenue lives only in the sales facts, so
-- joining events to sales cannot multiply it. Anything needing both aggregates events to the transaction first.
with events as (
    select * from {{ ref('stg_loyalty__events') }}
)

select
    event_id
    , member_id
    , event_type
    , event_at
    , cast(event_at as date) as event_date
    , transaction_id
    , entitlement_id
    , points
from events
