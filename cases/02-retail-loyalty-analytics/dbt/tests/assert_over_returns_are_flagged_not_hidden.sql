-- Invariant 8: where the returns of a sale line add up to more than was sold, the line that crosses the quantity
-- is flagged, and nothing else is. The excess stays in net sales: hiding it would make the till error disappear.
with lines as (
    select * from {{ ref('fct_sales_line') }}
)

, sold as (
    select transaction_id, line_number, quantity
    from lines
    where transaction_type = 'sale'
)

, returns_running as (
    select
        lines.sales_line_id
        , lines.is_over_return
        , sold.quantity as sold_quantity
        , sum(-lines.quantity) over (
            partition by lines.original_transaction_id, lines.original_line_number
            order by lines.transaction_at, lines.transaction_id
            rows between unbounded preceding and current row
        ) as returned_so_far
    from lines
    inner join sold
        on lines.original_transaction_id = sold.transaction_id
        and lines.original_line_number = sold.line_number
    where lines.transaction_type = 'return'
)

select sales_line_id, sold_quantity, returned_so_far, is_over_return
from returns_running
where is_over_return <> (returned_so_far > sold_quantity)
union all
select sales_line_id, null, null, is_over_return
from lines
where is_over_return and (transaction_type <> 'return' or is_unmatched_return)
