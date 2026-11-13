"""Fixed historical A12 profile: disjoint keys at a fixed event-time density, not a latency workload."""

from datetime import UTC, datetime, timedelta

from payrecon.streaming.contract import encode, stamp
from payrecon.streaming.producer import DATASET_ID, publish

COHORTS = 8
PAYMENTS_PER_COHORT = 24
STRIDE_MINUTES = 7
ANCHOR = datetime(2026, 10, 15, 10, tzinfo=UTC)


def cohort_events(cohort: int) -> list[dict]:
    if type(cohort) is not int or not 0 <= cohort < COHORTS:
        raise ValueError(f"cohort must be an integer from 0 to {COHORTS - 1}")
    result = []
    for payment in range(PAYMENTS_PER_COHORT):
        payment_id = f"state-{cohort:02d}-{payment:03d}"
        for sequence in (1, 2):
            event_time = ANCHOR + timedelta(
                minutes=cohort * STRIDE_MINUTES, seconds=2 * payment + sequence - 1
            )
            result.append(
                dict(
                    event_id=f"{payment_id}-{sequence}",
                    payment_id=payment_id,
                    merchant_id="M001",
                    event_type="initiated"
                    if sequence == 1
                    else ("declined" if payment % 4 == 0 else "authorised"),
                    sequence_no=sequence,
                    event_time=stamp(event_time),
                    ingestion_time=stamp(event_time + timedelta(seconds=30)),
                    amount_minor=4200,
                    currency="GBP",
                )
            )
    return result


def produce_cohort(topic: str, cohort: int, dataset_id: str = DATASET_ID) -> dict:
    if not topic.startswith("case03.live-check-"):
        raise ValueError("state fixture requires a case03.live-check-* diagnostic topic")
    events = cohort_events(cohort)
    return publish(topic, [(e["payment_id"].encode(), encode(e, dataset_id)) for e in events])
