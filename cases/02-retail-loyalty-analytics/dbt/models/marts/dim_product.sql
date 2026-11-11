/*
    One row per product, including products the tills have sold but master data has not yet sent.
    Those get an inferred row so no sale is dropped or left without a product; when the master-data row arrives,
    the next build replaces the inferred row. Facts hold the business key, so they need no change.
*/
with products as (
    select * from {{ ref('stg_reference__products') }}
)

, transaction_lines as (
    select * from {{ ref('stg_pos__transaction_lines') }}
)

, known as (
    select
        product_id
        , product_name
        , category
        , list_price
        , false as is_inferred
    from products
)

, inferred as (
    select distinct
        transaction_lines.product_id
        , 'Unknown product ' || transaction_lines.product_id as product_name
        , 'Unknown' as category
        , cast(null as decimal(18, 2)) as list_price
        , true as is_inferred
    from transaction_lines
    left join products on transaction_lines.product_id = products.product_id
    where products.product_id is null
)

select product_id, product_name, category, list_price, is_inferred from known
union all
select product_id, product_name, category, list_price, is_inferred from inferred
