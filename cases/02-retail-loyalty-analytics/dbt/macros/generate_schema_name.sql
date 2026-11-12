{#
    Use the configured schema name as it is (staging, marts, snapshots, audit) instead of dbt's default
    "<target schema>_<custom schema>". One developer, one warehouse file: the prefix would only add noise.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
