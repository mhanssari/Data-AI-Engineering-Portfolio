-- Invariant 5: for each store, versions start on the opening date, follow one another with no gap or overlap,
-- and exactly one is current. Returns one row per broken store.
with versions as (
    select
        store_id
        , opened_date
        , valid_from_date
        , valid_to_date
        , is_current
        , lead(valid_from_date) over (partition by store_id order by valid_from_date) as next_valid_from_date
        , row_number() over (partition by store_id order by valid_from_date) as version_number
    from {{ ref('dim_store') }}
)

, per_store as (
    select
        store_id
        , sum(case when version_number = 1 and valid_from_date <> opened_date then 1 else 0 end) as starts_late_count
        , sum(case when next_valid_from_date <> valid_to_date then 1 else 0 end) as gap_or_overlap_count
        , sum(case when valid_from_date >= valid_to_date then 1 else 0 end) as empty_version_count
        , sum(case when is_current then 1 else 0 end) as current_version_count
    from versions
    group by store_id
)

select *
from per_store
where starts_late_count > 0
    or gap_or_overlap_count > 0
    or empty_version_count > 0
    or current_version_count <> 1
