-- One row: the date every "window closed", "eligible" and "mature" rule is measured against.
-- Derived from the data, not the clock, so a rebuild next year gives the same answer.
{% set override = var('as_of_date') %}

with transactions as (
    select * from {{ ref('stg_pos__transactions') }}
)

select
    {% if override %}
    cast('{{ override }}' as date) as as_of_date
    {% else %}
    max(transaction_date) as as_of_date
    {% endif %}
from transactions
