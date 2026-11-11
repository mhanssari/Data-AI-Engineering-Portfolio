with reward_entitlements as (
    select * from {{ source('loyalty', 'reward_entitlements') }}
)

, typed as (
    select
        entitlement_id
        , member_id
        , reward_type
        , cast(issued_at as timestamp) as issued_at
        , cast(expires_at as timestamp) as expires_at
        , status
        , cast(redeemed_at as timestamp) as redeemed_at
        , redeemed_transaction_id
        , cast(updated_at as timestamp) as updated_at
        , _load_id as source_load_id
    from reward_entitlements
)

-- deduplicate: each status change re-sends the entitlement; the latest state wins. The status column is kept
-- as delivered here; reconciling it with the event log is business logic and belongs downstream.
select *
from typed
qualify row_number() over (partition by entitlement_id order by updated_at desc, source_load_id desc) = 1
