"""Explicit abrupt-exit hooks, restricted to disposable diagnostic databases/topics."""

import json
import os
import re


def validate_crash(point: str | None, role: str, topic: str) -> None:
    if point is None:
        return
    if (
        point not in ("before-commit", "after-commit")
        or role != "live"
        or not topic.startswith("case03.live-check-")
        or not re.fullmatch(r"payments_stream_check_[a-f0-9]{12}", os.environ.get("PGDATABASE", ""))
    ):
        raise ValueError("crash injection requires a disposable live-check topic/database and live role")


def crash(point: str, generation: str, batch_id: int) -> None:
    print(json.dumps(dict(diagnostic_crash=point, generation=generation, batch_id=batch_id)), flush=True)
    # No exception handler, Spark stop or transaction context exit: Docker also stops PID 1's children.
    os._exit(86 if point == "before-commit" else 87)
