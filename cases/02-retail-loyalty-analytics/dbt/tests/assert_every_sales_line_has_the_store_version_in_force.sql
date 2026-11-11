-- Invariant 6: every sales line points at exactly the store version in force on its transaction date.
-- Catches a stale version left behind by an incremental run as well as a line with no version at all.
with lines as (
    select sales_line_id, store_id, store_version_id, transaction_date from {{ ref('fct_sales_line') }}
)

, in_force as (
    select lines.sales_line_id, stores.store_version_id
    from lines
    inner join {{ ref('dim_store') }} as stores
        on lines.store_id = stores.store_id
        and lines.transaction_date >= stores.valid_from_date
        and lines.transaction_date < stores.valid_to_date
)

, per_line as (
    select
        lines.sales_line_id
        , lines.store_version_id
        , count(in_force.store_version_id) as versions_in_force_count
        , max(in_force.store_version_id) as version_in_force
    from lines
    left join in_force on lines.sales_line_id = in_force.sales_line_id
    group by lines.sales_line_id, lines.store_version_id
)

select *
from per_line
where versions_in_force_count <> 1
    or version_in_force <> store_version_id
