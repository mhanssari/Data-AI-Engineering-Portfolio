with transaction_lines as (
    select * from {{ source('pos', 'transaction_lines') }}
)

, typed as (
    select
        transaction_id
        , cast(line_number as integer) as line_number
        , product_id
        , cast(quantity as integer) as quantity
        , cast(unit_price as decimal(18, 2)) as unit_price
        , cast(discount_amount as decimal(18, 2)) as discount_amount
        , currency_code
        , cast(original_line_number as integer) as original_line_number
        , _load_id as source_load_id
    from transaction_lines
)

-- deduplicate: lines arrive again whenever their transaction is re-delivered; the latest load wins.
select *
from typed
qualify row_number() over (partition by transaction_id, line_number order by source_load_id desc) = 1
