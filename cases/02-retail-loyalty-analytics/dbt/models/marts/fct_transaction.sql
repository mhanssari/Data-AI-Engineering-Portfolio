-- One row per till transaction, sale or return, rolled up from its lines. Amounts come only from fct_sales_line,
-- so the two facts cannot disagree (invariant 2 checks it). Rebuilt in full: it is small and derived.
with sales_lines as (
    select * from {{ ref('fct_sales_line') }}
)

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
