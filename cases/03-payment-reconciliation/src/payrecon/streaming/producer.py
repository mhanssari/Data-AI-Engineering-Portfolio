"""Acknowledged producer and a deliberately tiny intake fixture; optional Kafka imports are lazy."""

import json
import os

from payrecon.streaming.contract import encode
from payrecon.streaming.intake import Identity

DATASET_ID = "case03-intake-v1"


def bootstrap() -> str:
    return os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")


def identity(topic: str) -> Identity:
    from confluent_kafka import TopicCollection
    from confluent_kafka.admin import AdminClient

    if not topic.startswith("case03."):
        raise ValueError("streaming commands require a case03. topic")
    admin = AdminClient({"bootstrap.servers": bootstrap()})
    cluster = admin.describe_cluster(request_timeout=10).result()
    description = admin.describe_topics(TopicCollection([topic]), request_timeout=10)[topic].result()
    return Identity(cluster.cluster_id, str(description.topic_id), topic)


def fixture_records(dataset_id: str = DATASET_ID) -> list[tuple[bytes, bytes | None]]:
    initiated = dict(
        event_id="evt-900000201",
        payment_id="pay-9000002",
        merchant_id="M002",
        event_type="initiated",
        sequence_no=1,
        event_time="2026-10-03T11:00:00Z",
        ingestion_time="2026-10-03T11:00:30Z",
        amount_minor=4200,
        currency="EUR",
    )
    captured = dict(
        initiated,
        event_id="evt-900000203",
        event_type="captured",
        sequence_no=3,
        event_time="2026-10-03T11:10:00Z",
        ingestion_time="2026-10-03T11:10:30Z",
    )
    authorised = dict(
        initiated,
        event_id="evt-900000202",
        event_type="authorised",
        sequence_no=2,
        event_time="2026-10-03T11:00:05Z",
        ingestion_time="2026-10-04T11:00:05Z",
    )
    bad_amount = json.loads(encode(initiated, dataset_id))
    bad_amount["event"].update(event_id="evt-bad", payment_id="pay-bad", amount_minor="12,50")
    wrong_version = json.loads(encode(initiated, dataset_id))
    wrong_version["schema_version"] = 2
    key = b"pay-9000002"
    return [
        (key, encode(initiated, dataset_id)),
        (key, encode(captured, dataset_id)),
        (key, encode(captured, dataset_id)),
        (b"pay-json", b"{"),
        (b"pay-bad", json.dumps(bad_amount).encode()),
        (b"pay-tombstone", None),
        (key, json.dumps(wrong_version).encode()),
        (key, encode(authorised, dataset_id)),
    ]


def produce_fixture(topic: str, dataset_id: str = DATASET_ID) -> dict:
    if not topic.startswith("case03.intake-check"):
        raise ValueError("fixture producer writes only to case03.intake-check* topics")
    return publish(topic, fixture_records(dataset_id))


def publish(topic: str, records: list[tuple[bytes, bytes | None]]) -> dict:
    from confluent_kafka import Producer

    if not topic.startswith(("case03.intake-check", "case03.live-check")):
        raise ValueError("fixture publishing requires a case-owned diagnostic topic")
    source = identity(topic)
    producer = Producer(
        {
            "bootstrap.servers": bootstrap(),
            "enable.idempotence": True,
            "acks": "all",
            "delivery.timeout.ms": 30000,
            "request.timeout.ms": 10000,
        }
    )
    delivered, errors = [], []

    def acknowledged(error, message):
        if error:
            errors.append(str(error))
        else:
            delivered.append(dict(partition=message.partition(), offset=message.offset()))

    for key, value in records:
        producer.produce(topic, key=key, value=value, on_delivery=acknowledged)
        producer.poll(0)
    remaining = producer.flush(35)
    if errors or remaining or len(delivered) != len(records):
        raise RuntimeError(
            f"producer acknowledgements incomplete: {len(delivered)} acknowledged, {remaining} pending, {errors}"
        )
    return dict(
        cluster_id=source.cluster_id, topic_id=source.topic_id, acknowledged=len(delivered), offsets=delivered
    )
