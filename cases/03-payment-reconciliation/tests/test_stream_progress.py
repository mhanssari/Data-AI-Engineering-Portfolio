"""Regression: an idle report must not hide the real callback when Spark reuses the next batch ID."""

from payrecon.streaming.progress import callback_reports


def test_idle_poll_is_not_a_completed_callback_even_when_it_has_a_batch_id():
    idle = dict(
        batchId=16, numInputRows=0, durationMs=dict(latestOffset=10, triggerExecution=12), stateOperators=[]
    )
    assert list(callback_reports([idle])) == []


def test_actual_callback_after_idle_and_empty_watermark_cleanup_are_both_reported():
    idle = dict(batchId=16, numInputRows=0, durationMs=dict(triggerExecution=12))
    real = dict(batchId=16, numInputRows=48, durationMs=dict(addBatch=500, triggerExecution=520))
    cleanup = dict(
        batchId=17,
        numInputRows=0,
        durationMs=dict(addBatch=100, triggerExecution=120),
        stateOperators=[dict(numRowsTotal=144)],
    )
    assert list(callback_reports([idle, real, cleanup])) == [real, cleanup]
