-- Invariant 4: every mart that carries money agrees with fct_sales_line for the same slice, here per member
-- (anonymous sales as one group). This is what catches a join that fans out, such as loyalty events joined to
-- sales without aggregating them first. Extend the union when a new mart carries an amount.
with by_lines as (
    select coalesce(member_id, '(anonymous)') as member_key, sum(net_amount) as net_amount
    from {{ ref('fct_sales_line') }}
    group by 1
)

, by_mart as (
    select 'fct_transaction' as mart, coalesce(member_id, '(anonymous)') as member_key, sum(net_amount) as net_amount
    from {{ ref('fct_transaction') }}
    group by 1, 2
)

select
    by_mart.mart
    , coalesce(by_mart.member_key, by_lines.member_key) as member_key
    , by_lines.net_amount as sales_line_net_amount
    , by_mart.net_amount as mart_net_amount
from by_mart
full outer join by_lines on by_mart.member_key = by_lines.member_key
where by_mart.member_key is null
    or by_lines.member_key is null
    or by_mart.net_amount <> by_lines.net_amount
