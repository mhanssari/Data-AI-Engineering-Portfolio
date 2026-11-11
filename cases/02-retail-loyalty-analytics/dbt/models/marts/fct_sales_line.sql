/*
    One row per transaction line, sale or return, with the store version in force on the transaction date.

    Incremental (decision 0006). A run reprocesses whole transactions, chosen by load, not by date:
      1. transactions whose header or lines arrived since the last build (source_load_id above the watermark),
         which catches late returns and corrected headers whatever date they carry;
      2. returns that point at an affected sale, or at the same sale as an affected return, because
         is_over_return accumulates across all returns of a sale line;
      3. existing rows whose store version no longer matches dim_store, so a store version that arrives late
         repairs the sales it covers.
    delete+insert on transaction_id replaces every line of an affected transaction, so a line dropped from a
    corrected transaction disappears too. A full refresh must give the same table; tests check it.
*/
{{
    config(
        materialized='incremental',
        incremental_strategy='delete+insert',
        unique_key='transaction_id',
        on_schema_change='fail'
    )
}}

with lines as (
    select * from {{ ref('int_sales_lines_with_returns') }}
)

, stores as (
    select * from {{ ref('dim_store') }}
)

{% if is_incremental() %}
, watermark as (
    select coalesce(max(source_load_id), 0) as max_source_load_id from {{ this }}
)

, changed as (
    select distinct transaction_id, transaction_type, original_transaction_id
    from lines
    where source_load_id > (select max_source_load_id from watermark)
)

, affected_sales as (
    select transaction_id from changed
    union
    select original_transaction_id from changed where transaction_type = 'return'
)

, store_repairs as (
    select distinct existing.transaction_id
    from {{ this }} as existing
    inner join stores
        on existing.store_id = stores.store_id
        and existing.transaction_date >= stores.valid_from_date
        and existing.transaction_date < stores.valid_to_date
    where stores.store_version_id <> existing.store_version_id
)

, affected as (
    select transaction_id from changed
    union
    select transaction_id from lines where original_transaction_id in (select transaction_id from affected_sales)
    union
    select transaction_id from store_repairs
)
{% endif %}

select
    lines.sales_line_id
    , lines.transaction_id
    , lines.line_number
    , lines.transaction_type
    , lines.transaction_at
    , lines.transaction_date
    , lines.attributed_sale_date
    , lines.store_id
    , stores.store_version_id
    , lines.member_id
    , lines.is_member_sale
    , lines.product_id
    , lines.quantity
    , lines.unit_price
    , lines.gross_amount
    , lines.discount_amount
    , lines.net_amount
    , lines.currency_code
    , lines.original_transaction_id
    , lines.original_line_number
    , lines.is_unmatched_return
    , lines.is_over_return
    , lines.source_load_id
    , '{{ invocation_id }}' as built_in_invocation_id
from lines
inner join stores
    on lines.store_id = stores.store_id
    and lines.transaction_date >= stores.valid_from_date
    and lines.transaction_date < stores.valid_to_date
{% if is_incremental() %}
where lines.transaction_id in (select transaction_id from affected)
{% endif %}
