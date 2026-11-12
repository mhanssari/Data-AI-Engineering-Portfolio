{#
    ISO day of week, Monday = 1 to Sunday = 7. dbt has no cross-database macro for this, and engines spell it
    differently, so models call this and dbt picks the implementation for the target: snowflake__ on Snowflake,
    default__ (Postgres-style isodow, which DuckDB accepts) elsewhere.
#}
{% macro iso_day_of_week(date_expression) -%}
    {{ return(adapter.dispatch('iso_day_of_week', 'retail_loyalty')(date_expression)) }}
{%- endmacro %}

{% macro default__iso_day_of_week(date_expression) -%}
    cast(extract(isodow from {{ date_expression }}) as integer)
{%- endmacro %}

{% macro snowflake__iso_day_of_week(date_expression) -%}
    cast(dayofweekiso({{ date_expression }}) as integer)
{%- endmacro %}
