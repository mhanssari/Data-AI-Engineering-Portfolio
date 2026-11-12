"""Versioned event contract shared by the producer and durable intake; no Kafka/Spark imports."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from payrecon.config import CURRENCIES, EVENT_TYPES

FIELDS = {
    "event_id",
    "payment_id",
    "merchant_id",
    "event_type",
    "sequence_no",
    "event_time",
    "ingestion_time",
    "amount_minor",
    "currency",
}
ENVELOPE = {"schema_version", "dataset_id", "published_at", "event", "semantic_hash"}


class ContractError(ValueError):
    def __init__(self, reason: str, detail: str):
        self.reason = reason
        super().__init__(detail)


def utc(value: str) -> datetime:
    try:
        if not isinstance(value, str):
            raise ValueError
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
            raise ValueError
        return parsed.astimezone(UTC)
    except (ValueError, TypeError):
        raise ContractError(
            "invalid_clock", "timestamp must be ISO 8601 with an explicit UTC offset"
        ) from None


def stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def identifier(value, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value):
        raise ContractError("invalid_schema", f"{field} is not a valid identifier")
    return value


def normalise_event(event: dict) -> dict:
    if not isinstance(event, dict) or set(event) != FIELDS:
        raise ContractError("invalid_schema", "event fields do not match contract v1")
    result = dict(event)
    for field in ("event_id", "payment_id", "merchant_id"):
        identifier(result[field], field)
    for field, minimum, maximum in (("sequence_no", 1, 2**31 - 1), ("amount_minor", 0, 2**63 - 1)):
        if type(result[field]) is not int or not minimum <= result[field] <= maximum:
            raise ContractError("invalid_schema", f"{field} must be an integer in the permitted range")
    if result["event_type"] not in EVENT_TYPES or result["currency"] not in CURRENCIES:
        raise ContractError("invalid_schema", "unknown event type or currency")
    for field in ("event_time", "ingestion_time"):
        result[field] = stamp(utc(result[field]))
    if utc(result["event_time"]) > utc(result["ingestion_time"]):
        raise ContractError("invalid_clock", "event time is later than source ingestion time")
    return result


def semantic_hash(event: dict) -> str:
    return hashlib.sha256(json.dumps(event, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def encode(event: dict, dataset_id: str, published_at: datetime | None = None) -> bytes:
    event = normalise_event(event)
    envelope = {
        "schema_version": 1,
        "dataset_id": identifier(dataset_id, "dataset_id"),
        "published_at": stamp(published_at or datetime.now(UTC)),
        "event": event,
        "semantic_hash": semantic_hash(event),
    }
    return json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()


@dataclass(frozen=True)
class Dataset:
    dataset_id: str
    source_start: datetime
    source_end: datetime

    def __post_init__(self):
        identifier(self.dataset_id, "dataset_id")
        if (
            self.source_start.tzinfo is None
            or self.source_end.tzinfo is None
            or self.source_start >= self.source_end
        ):
            raise ValueError("dataset clock requires ordered timezone-aware bounds")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def decode(key: bytes | None, value: bytes | None, dataset: Dataset) -> tuple[dict, str]:
    if value is None:
        raise ContractError("tombstone", "null Kafka value")
    try:
        envelope = json.loads(value.decode("utf-8"), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, TypeError):
        raise ContractError("malformed_json", "value is not an unambiguous UTF-8 JSON object") from None
    if not isinstance(envelope, dict) or set(envelope) != ENVELOPE:
        raise ContractError("invalid_schema", "envelope fields do not match contract v1")
    if type(envelope["schema_version"]) is not int or envelope["schema_version"] != 1:
        raise ContractError("unknown_version", "only schema version 1 is supported")
    if envelope["dataset_id"] != dataset.dataset_id:
        raise ContractError("wrong_dataset", "envelope does not belong to the registered dataset")
    utc(envelope["published_at"])
    event = normalise_event(envelope["event"])
    if (
        not dataset.source_start
        <= utc(event["event_time"])
        <= utc(event["ingestion_time"])
        < dataset.source_end
    ):
        raise ContractError("invalid_clock", "source timestamps lie outside the registered dataset clock")
    try:
        key_text = key.decode("utf-8") if key is not None else None
    except UnicodeError:
        key_text = None
    if key_text != event["payment_id"]:
        raise ContractError("key_mismatch", "Kafka key must equal payment_id")
    digest = semantic_hash(event)
    if envelope["semantic_hash"] != digest:
        raise ContractError("hash_mismatch", "semantic event hash does not agree")
    return event, digest
