/*
    One row per till transaction, sale or return, rolled up from its lines, with the loyalty activity on it.
    Amounts come only from fct_sales_line (invariant 2). Events are summed to one row per transaction *before*
    the join, so a purchase with an earn, a bonus and a redemption event still counts its revenue once
    (invariant 4, planted D3). Rebuilt in full: it is small and derived.
*/
with sales_lines as (
    select * from {{ ref('fct_sales_line') }}
)

, events as (
    select * from {{ ref('fct_loyalty_event') }}
)

, transactions as (
    select
        transaction_id
        , transaction_type
        , min(transaction_at) as transaction_at
        , min(transaction_date) as transaction_date
        , min(attributed_sale_date) as attributed_sale_date
        , min(store_id) as store_id
        , min(store_version_id) as store_version_id
        , min(member_id) as member_id
        , max(case when is_member_sale then 1 else 0 end) = 1 as is_member_sale
        , cast(count(*) as integer) as line_count
        , cast(sum(quantity) as integer) as item_quantity
        , cast(sum(gross_amount) as decimal(18, 2)) as gross_amount
        , cast(sum(discount_amount) as decimal(18, 2)) as discount_amount
        , cast(sum(net_amount) as decimal(18, 2)) as net_amount
        , min(currency_code) as currency_code
        , min(original_transaction_id) as original_transaction_id
        , max(case when is_unmatched_return then 1 else 0 end) = 1 as is_unmatched_return
    from sales_lines
    group by transaction_id, transaction_type
)

, loyalty_per_transaction as (
    select
        transaction_id
        , cast(sum(case when event_type in ('points_earned', 'bonus_points', 'points_reversed') then points else 0 end)
            as integer) as points_earned
        , cast(sum(case when event_type = 'reward_redeemed' then 1 else 0 end) as integer) as reward_redemption_count
    from events
    where transaction_id is not null
    group by transaction_id
)

select
    transactions.transaction_id
    , transactions.transaction_type
    , transactions.transaction_at
    , transactions.transaction_date
    , transactions.attributed_sale_date
    , transactions.store_id
    , transactions.store_version_id
    , transactions.member_id
    , transactions.is_member_sale
    , transactions.line_count
    , transactions.item_quantity
    , transactions.gross_amount
    , transactions.discount_amount
    , transactions.net_amount
    , transactions.currency_code
    , transactions.original_transaction_id
    , transactions.is_unmatched_return
    , coalesce(loyalty_per_transaction.points_earned, 0) as points_earned
    , coalesce(loyalty_per_transaction.reward_redemption_count, 0) as reward_redemption_count
from transactions
left join loyalty_per_transaction on transactions.transaction_id = loyalty_per_transaction.transaction_id
