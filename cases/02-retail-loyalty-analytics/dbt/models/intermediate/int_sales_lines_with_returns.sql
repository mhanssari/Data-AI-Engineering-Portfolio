/*
    One row per transaction line with its header and amounts. A return line is linked to the sale line it
    returns, which gives it two things:

    - attributed_sale_date: the original sale's date, so cohort and product views can move the return back to
      the sale it undoes. Finance keeps the return on its own date (docs/metrics.md, net sales).
    - is_over_return: the returns of one sale line, accumulated in time order, exceed what was sold.

    A return whose sale line is not in the data is kept, flagged unmatched and attributed to its own date: dropping
    it would overstate net sales.
*/
with lines as (
    select * from {{ ref('stg_pos__transaction_lines') }}
)

, transactions as (
    select * from {{ ref('stg_pos__transactions') }}
)

, lines_with_headers as (
    select
        lines.transaction_id
        , lines.line_number
        , transactions.transaction_type
        , transactions.transaction_at
        , transactions.transaction_date
        , transactions.store_id
        , transactions.member_id
        , lines.product_id
        , lines.quantity
        , lines.unit_price
        , lines.discount_amount
        , lines.currency_code
        , transactions.original_transaction_id
        , lines.original_line_number
        -- A line is new to the warehouse when either it or its header was delivered again.
        , greatest(lines.source_load_id, transactions.source_load_id) as source_load_id
    from lines
    inner join transactions on lines.transaction_id = transactions.transaction_id
)

, sale_lines as (
    select transaction_id, line_number, transaction_date, quantity
    from lines_with_headers
    where transaction_type = 'sale'
)

, linked as (
    select
        lines_with_headers.*
        , sale_lines.transaction_date as original_transaction_date
        , sale_lines.quantity as original_quantity
        , sum(case when lines_with_headers.transaction_type = 'return' then -lines_with_headers.quantity end) over (
            partition by lines_with_headers.original_transaction_id, lines_with_headers.original_line_number
            order by lines_with_headers.transaction_at, lines_with_headers.transaction_id
            rows between unbounded preceding and current row
        ) as cumulative_returned_quantity
    from lines_with_headers
    left join sale_lines
        on lines_with_headers.original_transaction_id = sale_lines.transaction_id
        and lines_with_headers.original_line_number = sale_lines.line_number
)

select
    transaction_id || '-' || cast(line_number as varchar) as sales_line_id
    , transaction_id
    , line_number
    , transaction_type
    , transaction_at
    , transaction_date
    , case
        when transaction_type = 'return' and original_transaction_date is not null then original_transaction_date
        else transaction_date
    end as attributed_sale_date
    , store_id
    , member_id
    , member_id is not null as is_member_sale
    , product_id
    , quantity
    , unit_price
    , cast(quantity * unit_price as decimal(18, 2)) as gross_amount
    , discount_amount
    , cast(quantity * unit_price - discount_amount as decimal(18, 2)) as net_amount
    , currency_code
    , original_transaction_id
    , original_line_number
    , transaction_type = 'return' and original_transaction_date is null as is_unmatched_return
    , coalesce(transaction_type = 'return' and cumulative_returned_quantity > original_quantity, false)
        as is_over_return
    , source_load_id
from linked
