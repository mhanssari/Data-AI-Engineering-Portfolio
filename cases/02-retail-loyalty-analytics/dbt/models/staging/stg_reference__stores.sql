with stores as (
    select * from {{ source('reference', 'stores') }}
)

, typed as (
    select
        store_id
        , store_name
        , region
        , store_format
        , cast(opened_date as date) as opened_date
        , cast(effective_from_date as date) as effective_from_date
        , cast(updated_at as timestamp) as updated_at
        , _load_id as source_load_id
    from stores
)

-- deduplicate: every batch re-sends every version. One row per store and effective date; the latest delivery
-- wins, so a version corrected at source replaces the earlier one.
select *
from typed
qualify row_number() over (
    partition by store_id, effective_from_date order by updated_at desc, source_load_id desc
) = 1
