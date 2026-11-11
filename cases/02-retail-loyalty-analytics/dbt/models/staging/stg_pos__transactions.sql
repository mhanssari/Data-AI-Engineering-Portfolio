with transactions as (
    select * from {{ source('pos', 'transactions') }}
)

, typed as (
    select
        transaction_id
        , store_id
        , member_id
        , cast(transaction_at as timestamp) as transaction_at
        , cast(cast(transaction_at as timestamp) as date) as transaction_date
        , transaction_type
        , original_transaction_id
        , cast(updated_at as timestamp) as updated_at
        , _load_id as source_load_id
    from transactions
)

-- deduplicate: a retried extract repeats a row, and a correction re-sends it with a later updated_at.
-- The latest version wins; between identical versions, the later load.
select *
from typed
qualify row_number() over (partition by transaction_id order by updated_at desc, source_load_id desc) = 1
