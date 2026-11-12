# Tests

Run inside the case's Linux container, with PostgreSQL up (see the case README):

```sh
docker compose run --rm --entrypoint pytest payrecon -q
```

- `test_generator.py`: the same seed gives byte-identical files; every planted defect P1–P16 is delivered; the random background follows the payment state machine and settles exactly what was captured, at the plan's fee. A clean background means any exception raised downstream can only come from a planted record.
- `test_loader.py` (marker `db`): each test creates and drops its own database. Malformed rows are quarantined while the rest of the file lands; a re-sent file lands nothing; a corrected file under the same name does land; a damaged arrival (checksum, header, missing file, row count) is rejected whole; the full delivery loaded twice lands once.

- `test_core.py` (marker `db`): the state machine table matches the documented one; each distinct event is counted once (P1); every payment's state equals an independent Python replay of the generator's events in sequence order (invariant 2); an out-of-order event is held until the gap fills (P2); an illegal transition is recorded and not applied (P3); an over-refund is rejected; events for an unknown merchant wait in quarantine until it arrives (P5); a corrected settlement line supersedes the original; a sequence-number clash stops the build; core is referentially whole without foreign keys; rebuilding a day before and after later loads gives identical tables (invariant 8).

- `test_recon.py` (marker `db`): every run explains every difference (invariant 3); the last run has exactly the planted exceptions with their signed amounts; P2, P5 and P7 lines await for their grace day, then match or become exceptions; P6 is pending, then missing; P9 waits, P10's fee error is raised at once; P13 is a duplicate while P12's re-sent file is not; P14's late file makes 473 payments missing for one day; amounts are never added across currencies; rerunning a day gives identical rows; each check reports a deliberately corrupted result; a failed run publishes nothing and is logged.
