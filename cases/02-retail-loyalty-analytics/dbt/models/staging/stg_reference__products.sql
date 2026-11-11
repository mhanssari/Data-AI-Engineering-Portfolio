with products as (
    select * from {{ source('reference', 'products') }}
)

, typed as (
    select
        product_id
        , product_name
        , category
        , cast(list_price as decimal(18, 2)) as list_price
        , cast(updated_at as timestamp) as updated_at
        , _load_id as source_load_id
    from products
)

-- deduplicate: every batch re-sends every product; the latest delivery wins.
select *
from typed
qualify row_number() over (partition by product_id order by updated_at desc, source_load_id desc) = 1
