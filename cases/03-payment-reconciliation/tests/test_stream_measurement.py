import pytest

from payrecon.streaming.measurement import distribution, summarise


def sample(**changes):
    return dict(
        event_id="a",
        partition=0,
        offset=0,
        phase="measured",
        event_time="2026-11-12T10:00:00Z",
        broker_time="2026-11-12T10:00:01Z",
        observed_at="2026-11-12T10:00:03Z",
        **changes,
    )


def test_nearest_rank_uses_observations_and_excludes_warmup():
    assert distribution(list(range(1, 21))) == dict(n=20, p50=10, p95=19, maximum=20)
    warm = sample()
    warm.update(event_id="warm", offset=1, phase="warmup", observed_at="2026-11-12T11:00:00Z")
    result = summarise([sample(), warm])
    assert result["broker_to_visible_seconds"] == dict(n=1, p50=2, p95=2, maximum=2)
    assert result["event_to_visible_seconds"]["p95"] == 3


def test_invalid_samples_do_not_silently_disappear_or_clamp():
    for values in ([], [-0.1], [float("nan")], [float("inf")]):
        with pytest.raises(ValueError):
            distribution(values)
    with pytest.raises(ValueError, match="unique"):
        summarise([sample(), sample()])
    row = sample()
    del row["observed_at"]
    with pytest.raises(KeyError):
        summarise([row])
    row = sample()
    row["broker_time"] = "2026-11-12T10:00:04Z"
    with pytest.raises(ValueError, match="non-negative"):
        summarise([row])
