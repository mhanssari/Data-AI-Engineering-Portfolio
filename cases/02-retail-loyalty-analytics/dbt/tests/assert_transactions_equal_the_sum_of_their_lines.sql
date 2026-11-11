-- Invariant 2: every transaction's amounts and line count equal the sum of its lines, and no line lacks a
-- transaction. Returns one row per disagreement.
with lines as (
    select
        transaction_id
        , count(*) as line_count
        , sum(net_amount) as net_amount
        , sum(gross_amount) as gross_amount
    from {{ ref('fct_sales_line') }}
    group by transaction_id
)

select
    coalesce(lines.transaction_id, transactions.transaction_id) as transaction_id
    , lines.net_amount as lines_net_amount
    , transactions.net_amount as transaction_net_amount
from lines
full outer join {{ ref('fct_transaction') }} as transactions
    on lines.transaction_id = transactions.transaction_id
where lines.transaction_id is null
    or transactions.transaction_id is null
    or lines.line_count <> transactions.line_count
    or lines.net_amount <> transactions.net_amount
    or lines.gross_amount <> transactions.gross_amount
