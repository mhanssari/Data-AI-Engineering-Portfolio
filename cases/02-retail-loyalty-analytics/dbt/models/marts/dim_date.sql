with spine as (
    -- dbt.date_spine is dispatched per adapter, so the calendar does not depend on DuckDB's generate_series.
    {{ dbt.date_spine(
        "day",
        "cast('" ~ var('calendar_start_date') ~ "' as date)",
        dbt.dateadd("day", 1, "cast('" ~ var('calendar_end_date') ~ "' as date)")
    ) }}
)

, as_of as (
    select as_of_date from {{ ref('int_as_of_date') }}
)

, calendar as (
    select
        cast(spine.date_day as date) as date_day
        , cast({{ dbt.date_trunc("month", "spine.date_day") }} as date) as month_start_date
        , cast(extract(year from spine.date_day) as integer) as year_number
        , cast(extract(month from spine.date_day) as integer) as month_number
        , {{ iso_day_of_week("spine.date_day") }} as iso_day_of_week
        , as_of.as_of_date
    from spine
    cross join as_of
)

select
    date_day
    , month_start_date
    , year_number
    , month_number
    , iso_day_of_week
    , iso_day_of_week >= 6 as is_weekend
    , date_day > as_of_date as is_after_as_of_date
    -- A month is complete only if the as-of date reaches its last day; the report marks the others as partial.
    , {{ dbt.dateadd("day", -1, dbt.dateadd("month", 1, "month_start_date")) }} <= as_of_date as is_complete_month
from calendar
