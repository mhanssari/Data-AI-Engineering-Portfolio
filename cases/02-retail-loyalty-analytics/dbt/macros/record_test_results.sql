{#
    Called from on-run-end. Appends one row per data test and unit test in this invocation to
    audit.dq_test_result, so data quality has a history in the warehouse, not only in a log file. Failing rows of
    each data test are in the audit table named by failures_relation (store_failures).

    A model cannot hold this: a test's result exists only after the run that builds the model has finished.
#}
{% macro dq_test_result_ddl() %}
    create schema if not exists audit;
    create table if not exists audit.dq_test_result (
        invocation_id varchar
        , run_started_at timestamp
        , unique_id varchar
        , test_name varchar
        , test_kind varchar
        , tested_node varchar
        , status varchar
        , failure_count bigint
        , failures_relation varchar
        , execution_seconds double
    )
{% endmacro %}

{% macro record_test_results(results) %}
    {%- if not execute -%}
        {{ return('') }}
    {%- endif -%}
    {%- set rows = [] -%}
    {%- for result in results if result.node.resource_type in ('test', 'unit_test') -%}
        {%- set node = result.node -%}
        {%- set depends_on = node.depends_on.nodes | map('replace', 'model.' ~ project_name ~ '.', '') | join(', ') -%}
        {%- if node.resource_type == 'unit_test' -%}
            {%- set kind, tested = 'unit', depends_on -%}
        {%- elif node.attached_node -%}
            {%- set kind, tested = 'generic', node.attached_node.split('.')[-1] -%}
        {%- else -%}
            {%- set kind, tested = 'singular', depends_on -%}
        {%- endif -%}
        {%- set failures = result.failures if result.failures is not none else 'null' -%}
        {%- set relation = "'" ~ node.relation_name ~ "'" if node.relation_name else 'null' -%}
        {%- do rows.append(
            "('" ~ invocation_id ~ "', cast('" ~ run_started_at.strftime('%Y-%m-%d %H:%M:%S') ~ "' as timestamp), '"
            ~ node.unique_id ~ "', '" ~ node.name ~ "', '" ~ kind ~ "', '" ~ tested ~ "', '" ~ result.status ~ "', "
            ~ failures ~ ", " ~ relation ~ ", " ~ (result.execution_time | round(3)) ~ ")"
        ) -%}
    {%- endfor -%}
    {%- if rows | length > 0 -%}
        insert into audit.dq_test_result values {{ rows | join(', ') }}
    {%- else -%}
        select 1
    {%- endif -%}
{% endmacro %}
