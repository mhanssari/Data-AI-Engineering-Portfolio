# Data contracts

Status: reserved for implementation (milestone 3).

Planned YAML contracts for the main data products: supplier (golden), purchase order, supplier invoice and match result. Each contract records owner, consumers, schema and keys, business definitions (linked to the SKOS vocabulary), quality thresholds, reconciliation tolerances, freshness SLA, access rules and sensitivity, and version with a change-notice rule. Tests will validate data against the contracts and fail on breaking schema changes.
