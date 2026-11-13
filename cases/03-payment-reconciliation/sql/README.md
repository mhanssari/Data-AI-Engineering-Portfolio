# SQL implementation

- Batch migrations: [landing/operations](migrations/001_raw_and_ops.sql), [sequence-derived core](migrations/002_core.sql), [reconciliation](migrations/003_recon.sql).
- Batch transformations: [as-of core rebuild](core/build.sql), [matching and exceptions](recon/reconcile.sql), [publication invariants](recon/checks.sql).
- Shared batch/live rules: [allowed transitions](shared/allowed_transitions.sql) and [sequence walk](shared/payment_walk.sql), included explicitly by their callers.
- Separate streaming database: [intake migration](stream/migrations/stream_001_intake.sql), [live migration](stream/migrations/stream_002_live.sql), [repair audit](stream/migrations/stream_003_recovery.sql) and [affected-history projections](stream/project_live.sql).

Use the CLI's documented migration/build/reconcile commands; the Python wrapper controls transaction boundaries and SQL includes. Grains are owned by the [data model](../docs/data-model.md) and [streaming design](../docs/streaming-design.md); metric definitions are in the [catalogue](../docs/metrics.md).
