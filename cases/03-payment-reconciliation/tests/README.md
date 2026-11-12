# Tests

Run inside the case's Linux container, with PostgreSQL up (see the case README):

```sh
docker compose run --rm --entrypoint pytest payrecon -q
```

- `test_generator.py`: the same seed gives byte-identical files; every planted defect P1–P16 is delivered; the random background follows the payment state machine and settles exactly what was captured, at the plan's fee. A clean background means any exception raised downstream can only come from a planted record.
- `test_loader.py` (marker `db`): each test creates and drops its own database. Malformed rows are quarantined while the rest of the file lands; a re-sent file lands nothing; a corrected file under the same name does land; a damaged arrival (checksum, header, missing file, row count) is rejected whole; the full delivery loaded twice lands once.

Tests for payment state, matching, exceptions and control totals are added with those stages.
