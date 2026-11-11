/*
    One row per entitlement with a resolved status, because the delivered status column is not reliable: it is
    updated by a separate job that sometimes fails (D13, D14). The event log is the authority for redemption; the
    expiry date and the as-of date decide expiry. Rules, in order:

      redeemed   a reward_redeemed event exists (whatever the status column says)
      cancelled  the status column says cancelled (only the status system records cancellation)
      expired    expires_at falls on or before the end of the as-of date
      open       otherwise

    Where the status column disagrees with the resolved status, the row is flagged with the reason, so the
    disagreement is counted rather than silently corrected.
*/
with entitlements as (
    select * from {{ ref('stg_loyalty__reward_entitlements') }}
)

, events as (
    select * from {{ ref('stg_loyalty__events') }}
)

, as_of as (
    select as_of_date from {{ ref('int_as_of_date') }}
)

, redemptions as (
    select
        entitlement_id
        , event_at as redeemed_at
        , transaction_id as redeemed_transaction_id
    from events
    where event_type = 'reward_redeemed'
    qualify row_number() over (partition by entitlement_id order by event_at, event_id) = 1
)

, resolved as (
    select
        entitlements.entitlement_id
        , entitlements.member_id
        , entitlements.reward_type
        , entitlements.issued_at
        , entitlements.expires_at
        , entitlements.status as source_status
        , redemptions.redeemed_at
        , redemptions.redeemed_transaction_id
        , case
            when redemptions.entitlement_id is not null then 'redeemed'
            when entitlements.status = 'cancelled' then 'cancelled'
            when entitlements.expires_at < {{ dbt.dateadd("day", 1, "as_of.as_of_date") }} then 'expired'
            else 'open'
        end as resolved_status
    from entitlements
    cross join as_of
    left join redemptions on entitlements.entitlement_id = redemptions.entitlement_id
)

select
    entitlement_id
    , member_id
    , reward_type
    , issued_at
    , expires_at
    , source_status
    , redeemed_at
    , redeemed_transaction_id
    , resolved_status
    , case
        when resolved_status = 'redeemed' and source_status <> 'redeemed' then 'redemption_missing_from_status'
        when source_status = 'redeemed' and resolved_status <> 'redeemed' then 'status_redeemed_without_event'
        when resolved_status = 'expired' and source_status = 'issued' then 'expiry_missing_from_status'
        when resolved_status = 'open' and source_status <> 'issued' then 'status_closed_before_expiry'
    end as status_conflict_reason
from resolved
