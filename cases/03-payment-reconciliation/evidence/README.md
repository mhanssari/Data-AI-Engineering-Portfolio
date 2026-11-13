# Evidence

Recorded local validation and CI results, with scope and limitations in each report:

- [Final local MVP review and handoff](final-review-run.md) and [machine-readable summary](final-review-summary.json)

- [Clean batch setup](batch-clean-setup-run.md) and [database CI](ci-local-run.md)
- [Shared Airflow late-file recovery](airflow-late-file-run.md)
- [Spark durable intake](stream-intake-run.md), [live projections](stream-live-run.md) and [controlled recovery](stream-recovery-run.md)
- [Continuous state eviction](stream-state-run.md)
- [Current-clock latency/throughput measurement](stream-measurement-run.md) and [exact summary](stream-measurement-summary.json)

Generated per-event reports and logs live under ignored `ci/`. Read the reports for actual counts and timings; design targets are not measured results.
