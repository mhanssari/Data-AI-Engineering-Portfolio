"""Measurement arithmetic; observations are committed-row visibility, never transaction-start times."""

import math

from payrecon.streaming.contract import utc


def distribution(values: list[float]) -> dict:
    if not values or any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError("latency samples must be non-empty, finite and non-negative")
    ordered = sorted(values)
    return dict(
        n=len(ordered),
        p50=ordered[math.ceil(0.50 * len(ordered)) - 1],
        p95=ordered[math.ceil(0.95 * len(ordered)) - 1],
        maximum=ordered[-1],
    )


def summarise(records: list[dict]) -> dict:
    """One physical delivery per unique event; reject incomplete/ambiguous measurement joins."""
    ids = [r["event_id"] for r in records]
    coordinates = [(r["partition"], r["offset"]) for r in records]
    if len(set(ids)) != len(ids) or len(set(coordinates)) != len(coordinates):
        raise ValueError("measurement requires unique event IDs and transport coordinates")
    measured = [r for r in records if r["phase"] == "measured"]
    broker, event = [], []
    for row in measured:
        observed = utc(row["observed_at"])
        broker.append((observed - utc(row["broker_time"])).total_seconds())
        event.append((observed - utc(row["event_time"])).total_seconds())
    return dict(broker_to_visible_seconds=distribution(broker), event_to_visible_seconds=distribution(event))
