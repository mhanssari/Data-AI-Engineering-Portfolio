# Architecture decisions

Portfolio-wide infrastructure ownership is defined in [portfolio decision 0001](../../../../platform/decisions/0001-shared-infrastructure.md): shared Airflow and future Kafka under `platform/`, with separate case workloads and state.

| # | Decision | Status |
| --- | --- | --- |
| [0001](0001-shared-airflow.md) | One shared Airflow for the portfolio | Accepted |
| [0002](0002-money-in-minor-units.md) | Money in integer minor units, one currency at a time | Accepted |
| [0003](0003-docker-required.md) | Docker is required for this case | Accepted |
| [0004](0004-core-rebuilt-as-of-arrival.md) | Core rebuilt whole from raw, as of an arrival date | Accepted |
| [0005](0005-reconciliation-runs-and-grace.md) | Reconciliation runs, the grace period, and lines that wait | Accepted |
| [0006](0006-batch-orchestration.md) | Bounded arrival scheduling on the shared Airflow | Accepted |
| [0007](0007-streaming-correctness-and-recovery.md) | Durable intake, bounded live state and transactional recovery | Proposed engineering design; metric and lateness confirmed |
| [0008](0008-streaming-runtime-and-intake.md) | Optional streaming runtime and durable intake | Accepted; intake implemented |
| [0009](0009-live-state-and-operational-projections.md) | Watermarked live admission and deterministic SQL projections | Accepted; live slice implemented |
| [0010](0010-controlled-stream-recovery.md) | Abrupt consumer recovery and bounded late backfill | Accepted; recovery slice implemented |
| [0011](0011-observed-stream-state-bounds.md) | Observe state eviction through a continuous query | Accepted; bounded A12 profile |
| [0012](0012-current-clock-measurement.md) | Paced current-clock measurement with independent visibility observations | Accepted; finite local profile |

Streaming ownership also follows [portfolio decision 0002](../../../../platform/decisions/0002-shared-kafka-topology.md). Refine proposed details against the first compatibility tests; record actual guarantees and recovery evidence when implemented.

Copy [the template](TEMPLATE.md) to a numbered file when a material choice is made. Status values: proposed, accepted or superseded.
