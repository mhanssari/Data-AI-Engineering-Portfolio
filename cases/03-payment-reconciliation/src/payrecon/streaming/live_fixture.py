"""Tiny phased historical replay: gaps, decisions, expiry, poison, reference lag and observed late drop."""

from payrecon.streaming.contract import encode
from payrecon.streaming.producer import DATASET_ID, publish


def event(
    payment: str,
    sequence: int,
    kind: str,
    minute: int,
    *,
    merchant="M001",
    amount=4200,
    ingestion: str | None = None,
) -> dict:
    return dict(
        event_id=f"live-{payment}-{sequence}",
        payment_id=f"live-{payment}",
        merchant_id=merchant,
        event_type=kind,
        sequence_no=sequence,
        event_time=f"2026-10-13T10:{minute:02d}:00Z",
        ingestion_time=ingestion or f"2026-10-13T10:{minute:02d}:30Z",
        amount_minor=amount,
        currency="GBP",
    )


def phase_records(phase: str, dataset_id: str = DATASET_ID) -> list[tuple[bytes, bytes | None]]:
    if phase == "initial":
        events = [
            event("gap", 1, "initiated", 0),
            event("gap", 3, "captured", 2),
            event("decline", 1, "initiated", 0),
            event("decline", 2, "declined", 1),
            event("expiry", 1, "initiated", 0),
            event("expiry", 2, "authorised", 1),
            event("expiry", 3, "expired", 2),
            event("illegal", 1, "initiated", 0),
            event("illegal", 2, "captured", 1),
            event("unknown", 1, "initiated", 0, merchant="M041"),
            event("unknown", 2, "authorised", 1, merchant="M041"),
            event("gap", 3, "captured", 2),
        ]
        result = [(e["payment_id"].encode(), encode(e, dataset_id)) for e in events]
        future = dict(
            event("future", 1, "initiated", 0),
            event_time="2099-01-01T00:00:00Z",
            ingestion_time="2099-01-01T00:00:00Z",
        )
        return [*result, (b"live-json", b"{"), (b"live-future", encode(future, dataset_id))]
    if phase == "complete":
        events = [event("gap", 2, "authorised", 1, ingestion="2026-10-13T10:03:00Z")]
    elif phase == "repeat":
        events = [
            event("gap", 3, "captured", 2),
            event("gap", 2, "authorised", 1, ingestion="2026-10-13T10:03:00Z"),
        ]
    elif phase == "advance":
        events = [event("advance", 1, "initiated", 30)]
    elif phase == "late":
        events = [event("late", 1, "initiated", 5, ingestion="2026-10-13T11:00:00Z")]
    elif phase == "late-gap":
        events = [event("gap", 2, "authorised", 1, ingestion="2026-10-13T11:00:00Z")]
    else:
        raise ValueError("unknown live fixture phase")
    return [(e["payment_id"].encode(), encode(e, dataset_id)) for e in events]


def produce_phase(topic: str, phase: str, dataset_id: str = DATASET_ID) -> dict:
    if not topic.startswith("case03.live-check"):
        raise ValueError("live fixture writes only to case03.live-check* topics")
    return publish(topic, phase_records(phase, dataset_id))
