# Requirements

Status: draft; not yet agreed.

## Business problem

A fictional retailer has inconsistent definitions of active customers, loyalty participation and redemption. Marketing needs reliable cohorts and inactivity signals; store managers need comparable performance; finance needs sales totals that reconcile after returns.

## Proposed acceptance scenarios

Returns reduce the correct sales totals; several reward events do not multiply revenue; inactive-customer logic includes members with no purchases; immature cohorts are not treated as failed retention; rerunning a batch is stable; a late correction gives the same business result as a clean full rebuild; dashboard totals reconcile to reference SQL.

## To resolve at discovery

Define stakeholder decisions, included and excluded scope, data sources, refresh/latency expectations, environment constraints and a traceable acceptance checklist. Link each acceptance criterion to evidence when implemented.
