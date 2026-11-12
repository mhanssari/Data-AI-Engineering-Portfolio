/*
    One row per store per attribute version (type 2), rebuilt from the dated versions the source sends, so the
    history is exact and reproducible from a clean clone (decision 0002).

    Validity is half-open: valid_from_date <= date < valid_to_date. The current version ends on 9999-12-31 rather
    than null, so a fact joins to its version with one range predicate and BI tools need no null handling.
*/
with versions as (
    select * from {{ ref('stg_reference__stores') }}
)

, chained as (
    select
        store_id
        , store_name
        , region
        , store_format
        , opened_date
        , effective_from_date as valid_from_date
        , coalesce(
            lead(effective_from_date) over (partition by store_id order by effective_from_date)
            , cast('9999-12-31' as date)
        ) as valid_to_date
    from versions
)

select
    store_id || '@' || cast(valid_from_date as varchar) as store_version_id
    , store_id
    , store_name
    , region
    , store_format
    , opened_date
    , valid_from_date
    , valid_to_date
    , valid_to_date = cast('9999-12-31' as date) as is_current
from chained
