-- Invariant 4: every mart that carries money agrees with fct_sales_line for the same slice, here per member
-- (anonymous sales as one group). This is what catches a join that fans out, such as loyalty events joined to
-- sales without aggregating them first. Add a branch when a new mart carries an amount.
with by_lines as (
    select coalesce(member_id, '(anonymous)') as member_key, sum(net_amount) as net_amount
    from {{ ref('fct_sales_line') }}
    group by 1
)

, by_mart as (
    select 'fct_transaction' as mart, coalesce(member_id, '(anonymous)') as member_key, sum(net_amount) as net_amount
    from {{ ref('fct_transaction') }}
    group by 1, 2

    union all

    -- Member months hold member sales only, so the anonymous group is not expected here.
    select 'fct_member_month', member_id, sum(net_sales_amount)
    from {{ ref('fct_member_month') }}
    group by 1, 2
)

, expected as (
    select marts.mart, by_lines.member_key, by_lines.net_amount
    from by_lines
    cross join (select distinct mart from by_mart) as marts
    where not (marts.mart = 'fct_member_month' and by_lines.member_key = '(anonymous)')
)

select
    coalesce(by_mart.mart, expected.mart) as mart
    , coalesce(by_mart.member_key, expected.member_key) as member_key
    , expected.net_amount as sales_line_net_amount
    , by_mart.net_amount as mart_net_amount
from by_mart
full outer join expected
    on by_mart.mart = expected.mart
    and by_mart.member_key = expected.member_key
-- A member with no sales has zero-spend months in fct_member_month and no sales lines: absent means zero.
where coalesce(by_mart.net_amount, 0) <> coalesce(expected.net_amount, 0)
