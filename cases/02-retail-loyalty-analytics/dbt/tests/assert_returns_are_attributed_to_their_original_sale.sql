-- Invariant 3: the two date bases for net sales differ only by moving matched returns back to the date of the
-- sale they undo. Every matched return is attributed to its original sale's date, never after its own date;
-- every other line stays on its own date. Totals are then equal on both bases by construction.
with lines as (
    select * from {{ ref('fct_sales_line') }}
)

, originals as (
    select transaction_id, line_number, transaction_date
    from lines
    where transaction_type = 'sale'
)

select
    lines.sales_line_id
    , lines.transaction_date
    , lines.attributed_sale_date
    , originals.transaction_date as original_sale_date
from lines
left join originals
    on lines.original_transaction_id = originals.transaction_id
    and lines.original_line_number = originals.line_number
where
    (lines.transaction_type = 'return' and not lines.is_unmatched_return
        and (lines.attributed_sale_date <> originals.transaction_date
            or lines.attributed_sale_date > lines.transaction_date))
    or ((lines.transaction_type = 'sale' or lines.is_unmatched_return)
        and lines.attributed_sale_date <> lines.transaction_date)
    or (lines.is_unmatched_return and originals.transaction_id is not null)
