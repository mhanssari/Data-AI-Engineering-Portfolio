-- Invariant 9: each entitlement's resolved status follows the evidence, independently of how the model derives
-- it. Redeemed if and only if a redemption event exists; expired only after its expiry; open only before it;
-- and a redemption happens on one of the member's own transactions, within the reward's validity.
with entitlements as (
    select * from {{ ref('fct_reward_entitlement') }}
)

, redeemed_events as (
    select distinct entitlement_id from {{ ref('fct_loyalty_event') }} where event_type = 'reward_redeemed'
)

, as_of as (
    select max(date_day) as as_of_date from {{ ref('dim_date') }} where not is_after_as_of_date
)

, transactions as (
    select transaction_id, member_id from {{ ref('fct_transaction') }}
)

select
    entitlements.entitlement_id
    , entitlements.resolved_status
    , redeemed_events.entitlement_id is not null as has_redemption_event
    , transactions.member_id as redeeming_member_id
from entitlements
cross join as_of
left join redeemed_events on entitlements.entitlement_id = redeemed_events.entitlement_id
left join transactions on entitlements.redeemed_transaction_id = transactions.transaction_id
where (entitlements.resolved_status = 'redeemed') <> (redeemed_events.entitlement_id is not null)
    or (entitlements.resolved_status = 'expired' and entitlements.expires_date > as_of.as_of_date)
    or (entitlements.resolved_status = 'open' and entitlements.expires_date <= as_of.as_of_date)
    or (entitlements.resolved_status = 'redeemed'
        and (transactions.member_id is distinct from entitlements.member_id
            or entitlements.redeemed_at < entitlements.issued_at
            or entitlements.redeemed_at > entitlements.expires_at))
