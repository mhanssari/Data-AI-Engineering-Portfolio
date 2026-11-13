"""An unavailable database must fail CI, while optional local database checks may skip."""

import psycopg
import pytest
from conftest import fresh_database


@pytest.mark.parametrize("required", [False, True])
def test_unavailable_database_fails_when_required(monkeypatch: pytest.MonkeyPatch, required: bool) -> None:
    def unavailable(**kwargs):
        raise psycopg.OperationalError("connection refused (injected)")

    monkeypatch.setattr(psycopg, "connect", unavailable)
    if required:
        monkeypatch.setenv("PAYRECON_REQUIRE_DB", "1")
        outcome, message = pytest.fail.Exception, "PostgreSQL is required"
    else:
        monkeypatch.delenv("PAYRECON_REQUIRE_DB", raising=False)
        outcome, message = pytest.skip.Exception, "no PostgreSQL to test against"
    with pytest.raises(outcome, match=message), fresh_database("payrecon_test_requirement"):
        raise AssertionError("an unavailable database must never reach the test body")
