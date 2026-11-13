"""Prove that state-eviction input grows unique identity rather than recycling a small key set."""

from datetime import timedelta

import pytest

from payrecon.streaming.contract import Dataset, decode, encode, utc
from payrecon.streaming.state_profile import (
    COHORTS,
    PAYMENTS_PER_COHORT,
    STRIDE_MINUTES,
    cohort_events,
    produce_cohort,
)

DATASET = Dataset("case03-intake-v1", utc("2026-10-01T00:00:00Z"), utc("2026-11-01T00:00:00Z"))


def test_profile_has_disjoint_keys_valid_lifecycles_and_fixed_event_time_density():
    all_events = []
    previous = None
    for cohort in range(COHORTS):
        events = cohort_events(cohort)
        assert len(events) == 2 * PAYMENTS_PER_COHORT
        current = utc(events[0]["event_time"])
        if previous is not None:
            assert current - previous == timedelta(minutes=STRIDE_MINUTES)
        previous = current
        for e in events:
            restored, _ = decode(e["payment_id"].encode(), encode(e, DATASET.dataset_id), DATASET)
            assert restored == e
        assert sum(e["event_type"] == "declined" for e in events) == 6
        assert sum(e["event_type"] == "authorised" for e in events) == 18
        all_events.extend(events)
    assert len({e["event_id"] for e in all_events}) == 384
    assert len({e["payment_id"] for e in all_events}) == 192


def test_republished_cohort_preserves_semantic_identity():
    for a, b in zip(cohort_events(0), cohort_events(0), strict=True):
        key = a["payment_id"].encode()
        first = decode(key, encode(a, DATASET.dataset_id, utc("2026-11-12T00:00:00Z")), DATASET)
        repeated = decode(key, encode(b, DATASET.dataset_id, utc("2026-11-12T01:00:00Z")), DATASET)
        assert first == repeated


def test_state_fixture_refuses_the_primary_topic_before_contacting_kafka():
    with pytest.raises(ValueError, match="diagnostic"):
        produce_cohort("case03.payment-events.v1", 0)
