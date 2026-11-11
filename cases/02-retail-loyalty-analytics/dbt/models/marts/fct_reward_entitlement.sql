-- One row per entitlement (accumulating snapshot): its milestone dates, the resolved status and, if redeemed,
-- where. The redemption-rate denominator is is_resolved: an open entitlement has not had its chance yet.
with entitlements as (
    select * from {{ ref('int_entitlements_resolved') }}
)

, transactions as (
    select transaction_id, store_id, store_version_id from {{ ref('fct_transaction') }}
)

select
    entitlements.entitlement_id
    , entitlements.member_id
    , entitlements.reward_type
    , entitlements.issued_at
    , cast(entitlements.issued_at as date) as issued_date
    , cast({{ dbt.date_trunc("month", "entitlements.issued_at") }} as date) as issue_month_start_date
    , entitlements.expires_at
    , cast(entitlements.expires_at as date) as expires_date
    , entitlements.redeemed_at
    , cast(entitlements.redeemed_at as date) as redeemed_date
    , entitlements.redeemed_transaction_id
    , transactions.store_id as redeemed_store_id
    -- The store version in force when redeemed, so the report relates redemptions to dim_store like sales.
    , transactions.store_version_id as redeemed_store_version_id
    , entitlements.source_status
    , entitlements.resolved_status
    , entitlements.resolved_status in ('redeemed', 'expired') as is_resolved
    , entitlements.resolved_status = 'redeemed' as is_redeemed
    , entitlements.status_conflict_reason is not null as has_status_conflict
    , entitlements.status_conflict_reason
from entitlements
left join transactions on entitlements.redeemed_transaction_id = transactions.transaction_id
