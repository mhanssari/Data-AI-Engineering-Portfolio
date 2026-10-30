# Requirements

Status: draft; not yet agreed.

## Business problem

A fictional consumer-goods company cannot consistently identify stockout exposure, slow-moving inventory and changes in demand assumptions across warehouses. Supply-chain planners need daily exception lists; finance needs traceable inventory values; managers need trends without double-counting snapshots.

## Proposed acceptance scenarios

Known fixture stock and value reconcile to source; two snapshot dates do not double stock; duplicate files leave final totals unchanged; a late correction updates the intended date; zero-demand items remain visible with an explicit interpretation; unauthorised warehouse rows are excluded by the tested security configuration.

## To resolve at discovery

Define stakeholder decisions, included and excluded scope, data sources, refresh/latency expectations, environment constraints and a traceable acceptance checklist. Link each acceptance criterion to evidence when implemented.
