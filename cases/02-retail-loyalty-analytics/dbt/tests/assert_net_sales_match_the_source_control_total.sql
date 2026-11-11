-- Invariant 1: line count and net sales in fct_sales_line equal a control total computed straight from raw, with
-- its own deduplication, independently of the staging and intermediate models. A line lost to a join (for
-- example a sale before its store opened) or doubled by a fan-out shows up here. Returns a row on mismatch.
with raw_lines as (
    select
        transaction_id
        , line_number
        , cast(quantity as integer) * cast(unit_price as decimal(18, 2)) - cast(discount_amount as decimal(18, 2))
            as net_amount
        , row_number() over (partition by transaction_id, line_number order by _load_id desc) as delivery_rank
    from {{ source('pos', 'transaction_lines') }}
)

, control as (
    select count(*) as line_count, sum(net_amount) as net_amount
    from raw_lines
    where delivery_rank = 1
)

, modelled as (
    select count(*) as line_count, sum(net_amount) as net_amount
    from {{ ref('fct_sales_line') }}
)

select
    control.line_count as control_line_count
    , modelled.line_count as modelled_line_count
    , control.net_amount as control_net_amount
    , modelled.net_amount as modelled_net_amount
from control
cross join modelled
where control.line_count <> modelled.line_count
    or control.net_amount <> modelled.net_amount
