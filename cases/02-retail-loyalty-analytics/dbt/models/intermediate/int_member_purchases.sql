/*
    One row per qualifying purchase: a member's sale, on or after enrolment, whose net amount after the returns
    attributed to it is above zero (docs/metrics.md). A purchase returned in full does not count, so a member who
    buys and returns everything is not "active". Numbered in time order per member, so purchase 1 is the first.

    Returns are attributed to their sale whenever they arrive, so a late return can stop an old purchase
    qualifying. Every metric built on this is therefore "as known at the as-of date".
*/
with sales_lines as (
    select * from {{ ref('fct_sales_line') }}
)

, members as (
    select member_id, enrolled_date from {{ ref('dim_member') }}
)

, sales as (
    select
        transaction_id
        , member_id
        , min(transaction_at) as purchase_at
        , min(transaction_date) as purchase_date
        , sum(net_amount) as sale_net_amount
    from sales_lines
    where transaction_type = 'sale' and member_id is not null
    group by transaction_id, member_id
)

, returned as (
    select original_transaction_id as transaction_id, sum(net_amount) as returned_net_amount
    from sales_lines
    where transaction_type = 'return' and not is_unmatched_return
    group by original_transaction_id
)

, qualifying as (
    select
        sales.transaction_id
        , sales.member_id
        , sales.purchase_at
        , sales.purchase_date
        , sales.sale_net_amount + coalesce(returned.returned_net_amount, 0) as net_amount_after_returns
    from sales
    inner join members on sales.member_id = members.member_id
    left join returned on sales.transaction_id = returned.transaction_id
    where sales.purchase_date >= members.enrolled_date
        and sales.sale_net_amount + coalesce(returned.returned_net_amount, 0) > 0
)

select
    transaction_id
    , member_id
    , purchase_at
    , purchase_date
    , net_amount_after_returns
    , row_number() over (partition by member_id order by purchase_at, transaction_id) as purchase_number
from qualifying
