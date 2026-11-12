# Abrupt consumer recovery and controlled late backfill

Recorded 12 November 2026 on Windows/Docker Desktop, using Linux containers. **A10 and A11 passed on the bounded synthetic fixture:** actual consumer exits around commit, checkpoint replay, a watermark-excluded authorisation repaired, and state/minute parity with a separate batch run. Recovery changes remain uncommitted; the workflow's new recovery step/artifact has not run remotely. Sustained eviction (A12), benchmarks and general broker-loss recovery remain open.

## Previous slice delivered

Committed/pushed live work as `89e465b` to `case-03-payments`. Both resulting workflows passed: [database CI](https://github.com/mhanssari/Data-AI-Engineering-Portfolio/actions/runs/43262873619) and [Case 03 Streaming](https://github.com/mhanssari/Data-AI-Engineering-Portfolio/actions/runs/43262873686). Inspected the streaming logs and downloaded `case-03-live-progress`: 19 deliveries, 14 intake events, 13 live events, one actual watermark drop and one missing admission, correctly provisional. Report output/upload from the live slice is now verified; it does not validate this subsequent recovery code.

## Commands and scope

With the optional image built and shared Kafka/case PostgreSQL healthy, from the repository root:

```sh
python cases/03-payment-reconciliation/streaming/check_recovery.py
```

The host helper uses only standard-library Python and Docker. It created a unique topic/database/checkpoint root, used the public case CLI for processing/repair, and removed only its own resources. The batch parity check used a separate disposable PostgreSQL database and temporary source files. Original portfolio databases, topics and checkpoints were preserved.

Actual check identity:

- Topic `case03.live-check-f69292b7f5b7`, topic ID `Z4GWzAuRRz6ukBa9ePpV3Q`; shared cluster ID `MkU3OEVBNTcwNTJENDM2Qk`.
- Live generation `e9f33253-66ce-41d9-b198-44ac7f3c6a99`; intake generation `11a3359b-7ace-42d7-8736-3ae76478c562`.
- Spark live query ID `58b4989d-12b2-4d28-8a03-5d2a1b139a12` persisted through both failures, repair and restart.
- Dataset `case03-intake-v1`, source clock `[2026-10-01T00:00:00Z, 2026-11-01T00:00:00Z)`; seed-3303 reference arrival 12 October, content hash `c403fc349b71eb46d9c8fc9d85ccfbb99f7b003f435f6444c16d8d24c412a6de`.

## A10: actual abrupt exits

| Boundary | Observed database/checkpoint facts | Restart result |
| --- | --- | --- |
| Before PostgreSQL commit, batch 0 | Process exit 86 after ledger/projections/batch record were written inside the open transaction; all four business tables and live batch ledger remained empty; Spark offset file existed and commit file did not | Same generation/query/checkpoint recovered all 11 canonical initial events; Spark commit file appeared |
| After PostgreSQL commit, batch 2 | Process exit 87 before callback return; 12 live events and the batch transaction persisted; Spark offset file existed and commit file did not | Callback reported `reused=true`; all four business-table fingerprints and the original batch commit record/timestamp stayed identical; Spark commit file appeared |

The injected exits bypassed normal exception cleanup and Spark stop. The second batch's stored input hash was `5292e76b46e1fa8ab4290f20f7aed2fb7cb1943ae6daed43e49148e6ac70ccef`; original commit timestamp `2026-11-12 17:06:06.337745+00:00` was preserved. This demonstrates the tested database/checkpoint recovery boundary, not end-to-end exactly-once delivery or recovery from arbitrary checkpoint corruption.

## A11: controlled late authorisation repair

After valid event-time advancement, published the missing authorisation behind the observed watermark. Intake retained it; live omitted it and kept its capture held. Both readers then reported the same exclusive frontier: partition 0 = 1, partition 1 = 5, partition 2 = 10, totalling 16 deliveries. Status was provisional with one missing admitted event.

Captured a JSON plan with the exact generation/epoch/source clock/frontier/reference. With both readers stopped, `stream-backfill` fenced intake/live/reference changes, revalidated the complete zero-based raw prefix against canonical intake, admitted the one missing event with origin `backfill`, and rebuilt the generation's SQL projections with its audit in one transaction. Repair ID: `recovery-f69292b7f5b7`.

| Result | Before | After |
| --- | ---: | ---: |
| Admitted events | 12 | 13 |
| Application rows | 12 | 13 |
| Payment states | 6 | 6 |
| Stored minute/outcome groups | 9 | 9 |
| Missing admitted events | 1 | 0 |

The held payment became captured with 4,200 GBP minor units. Minute buckets were replaced; this includes changing the held capture to an applied capture and adding the historical authorisation. The independent batch run matched **every payment-state business column for all six payments**, all **eight applied-event minute groups** (12 applied events), and the one illegal event. Eligibility was derived from batch event/state/illegal-transition tables, independently of the live application table. The ninth stored live minute/outcome group is the explicit illegal-event diagnostic.

The live checkpoint/sidecar hash was identical immediately before and after repair. Repeating the same repair ID/request returned `reused=true` with unchanged business fingerprints. Restart resumed the original Spark query/checkpoint without changing business rows. Re-delivering the repaired authorisation added transport evidence only: business fingerprints stayed unchanged and both reported frontiers advanced consistently to partition 0 = 1, partition 1 = 5, partition 2 = 11. Status remained consistent at that recorded frontier and exposed the repair ID/scope.

The helper printed all four PASS messages and `Recovery integration checks passed`, then exited 0 including scoped cleanup. Its full local report is ignored output at `evidence/ci/recovery-integration.json`; the new workflow is configured to upload `case-03-recovery-progress`. Remote execution/upload of that new step remains unverified until the recovery commit is pushed.

## Database tests and upgrade

From the case directory:

```sh
docker compose run --rm --no-deps -e PAYRECON_REQUIRE_DB=1 --entrypoint pytest payrecon -q --junitxml=evidence/ci/recovery-pytest.xml
```

**92 passed in 618.32 seconds, with PostgreSQL required; zero failures, errors or skips.** JUnit confirms those counts. This ran alongside real Spark checks and image build work, so the elapsed time is not a comparable performance benchmark. The 14 added checks cover exact repair eligibility/frontiers/reference/epoch, raw-prefix and canonical integrity, both-reader fencing, existing timestamp preservation, repair-ID replay safety, audit-failure rollback and crash-option restrictions. Focused recovery tests initially passed 14 in 11.64 seconds. After strict JSON plan typing and latest-repair status were finalised, all 45 streaming tests passed in 29.54 seconds.

Applied additive `stream_003_recovery` to the retained `payments_stream` demo. Existing live generation `053c312a-efe3-4eb4-8a48-4699689ca334` remained consistent at its recorded frontier (0 = 0, 1 = 2, 2 = 14), with three applied live events and no repair. Retained intake remains 16 deliveries, three canonical events and eight quarantine rows; canonical digest `1f27ca689a124e180370e8a6b0cea38b` is unchanged. No demo business row was reset or repaired unnecessarily.

Final validation: Ruff lint passed; formatting reported 81 files already formatted; base/optional Compose configurations and `actionlint` passed. Git whitespace checks passed and all 137 relative Markdown links in changed/new documentation and local handoff files resolved. Both final images built. Image-only probes imported recovery without Spark/Kafka/JRE in the batch image, started CLI help with Kafka stopped, and verified optional-image recovery modules, migration and check/parity helpers. The primary `case03.payment-events.v1` topic still has zero offsets in all three partitions.

## Limits and next step

The [decision](../docs/decisions/0010-controlled-stream-recovery.md) and [run guide](../streaming/README.md) own the implemented protocol. This repair requires paused readers, one complete current topic prefix, matching canonical history and an explicit reference frontier; limits are 20,000 deliveries and 64 MiB of key/value bytes. It is not general partial/multi-epoch/retention-loss recovery. Failed repairs roll back; errors remain in caller logs rather than committed failed-audit rows. Broker head may advance beyond the recorded frontier.

Next: commit/push recovery and confirm its new CI step/artifact, then test repeated watermark eviction (A12) before measurement/full-month replay. No new business decision blocks that work. Shared Kafka is stopped at handoff with all shared/case state preserved; PostgreSQL remains running and Airflow remains stopped.
