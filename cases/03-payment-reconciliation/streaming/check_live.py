"""Real Kafka/Spark check for the watermarked live view. Host stdlib and Docker only."""

import json
import uuid

from check_intake import CASE, COMPOSE, PLATFORM, run


def main():
    token = uuid.uuid4().hex[:12]
    topic, database = f"case03.live-check-{token}", f"payments_stream_check_{token}"
    checkpoint = f"/state/check-{token}"

    def stream(*args, python=False):
        command = COMPOSE + ["run", "--rm", "-T", "-e", f"PGDATABASE={database}"]
        if python:
            command += ["--entrypoint", "python"]
        return run(command + ["stream", *args]).stdout

    def drain(role):
        stream(f"stream-{role}", "--topic", topic, "--checkpoint", f"{checkpoint}/{role}", "--available-now")

    def phase(name, count):
        reply = json.loads(stream("stream-produce-live-fixture", "--topic", topic, "--phase", name))
        assert reply["acknowledged"] == count, reply
        drain("intake")
        drain("live")

    def snapshot():
        return json.loads(stream("/app/streaming/probe_live.py", python=True))

    def decision(s):
        return next(m for m in s["minutes"] if m["minute"] == "2026-10-13 10:01" and m["merchant"] == "M001")

    prepared = False
    try:
        run(
            PLATFORM
            + [
                "run",
                "--rm",
                "-T",
                "-e",
                f"PAYRECON_KAFKA_TOPIC={topic}",
                "-v",
                f"{CASE / 'streaming'}:/case:ro",
                "tools",
                "/case/init-kafka.sh",
            ]
        )
        stream("stream-prepare")
        prepared = True
        stream("stream-reference")
        phase("initial", 14)
        first = snapshot()
        assert first["counts"] == {
            "stream.delivery": 14,
            "stream.intake_event": 11,
            "stream.quarantine": 2,
            "stream.live_event": 11,
        }, first
        assert first["payments"]["live-gap"] == dict(status="initiated", held=1, captured=0), first
        assert first["diagnostics"] == dict(applied=7, held=1, illegal=1, unknown_reference=2), first
        assert float(decision(first)["decline_rate_pct"]) == 50, first
        assert max(first["watermarks"]) < "2026-10-13T10:03:00", first
        print(
            "PASS: gap held, illegal capture excluded, expiry separate, poison/future clock quarantined before watermark",
            flush=True,
        )
        drain("live")
        assert snapshot()["fingerprints"] == first["fingerprints"], (
            "checkpoint restart changed live business rows"
        )
        print(
            "PASS: stateful live checkpoint restart leaves all business rows and first-commit metadata unchanged",
            flush=True,
        )
        phase("complete", 1)
        complete = snapshot()
        assert complete["payments"]["live-gap"] == dict(status="captured", held=0, captured=4200), complete
        assert abs(float(decision(complete)["decline_rate_pct"]) - 100 / 3) < 1e-8, complete
        print("PASS: sequence gap filled; SQL reapplies history and replaces old minute buckets", flush=True)
        phase("repeat", 2)
        assert snapshot()["fingerprints"] == complete["fingerprints"], "republication inflated live totals"
        print("PASS: cross-batch duplicates change no canonical or projection row (A9 fixture)", flush=True)
        stream("stream-reference", "--arrival", "2026-10-12")
        refreshed = snapshot()
        assert refreshed["diagnostics"] == dict(applied=11, illegal=1), refreshed
        assert abs(float(decision(refreshed)["decline_rate_pct"]) - 100 / 3) < 1e-8, refreshed
        assert (
            float(
                next(
                    m
                    for m in refreshed["minutes"]
                    if m["minute"] == "2026-10-13 10:01" and m["merchant"] == "M041"
                )["decline_rate_pct"]
            )
            == 0
        ), refreshed
        assert refreshed["consistent_at_recorded_frontier"], refreshed
        print(
            "PASS: independent reference refresh applies held merchant history; rates remain per merchant and expiry stays separate",
            flush=True,
        )
        phase("advance", 1)
        advanced = snapshot()
        assert max(advanced["watermarks"]) >= "2026-10-13T10:20:00", advanced
        phase("late", 1)
        late = snapshot()
        assert late["counts"]["stream.intake_event"] == 14 and late["counts"]["stream.live_event"] == 13, late
        assert late["intake_events_not_admitted"] == 1 and not late["consistent_at_recorded_frontier"], late
        assert sum(s.get("numRowsDroppedByWatermark", 0) for s in late["state_operators"]) >= 1, late
        print(
            "PASS: observed watermark excludes one later-arriving event; intake retains it and status requires repair",
            flush=True,
        )
        report = CASE / "evidence/ci/live-integration.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(late, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(
            json.dumps(
                dict(
                    counts=late["counts"],
                    diagnostics=late["diagnostics"],
                    watermark=max(late["watermarks"]),
                    watermark_drops=sum(
                        s.get("numRowsDroppedByWatermark", 0) for s in late["state_operators"]
                    ),
                    intake_events_not_admitted=late["intake_events_not_admitted"],
                    consistent_at_recorded_frontier=late["consistent_at_recorded_frontier"],
                ),
                sort_keys=True,
            ),
            flush=True,
        )
        print("Live integration checks passed", flush=True)
    finally:
        try:
            if prepared:
                stream("/app/streaming/probe_intake.py", "--cleanup-token", token, python=True)
        finally:
            run(
                PLATFORM
                + [
                    "exec",
                    "-T",
                    "kafka",
                    "/opt/kafka/bin/kafka-topics.sh",
                    "--bootstrap-server",
                    "kafka:9092",
                    "--delete",
                    "--if-exists",
                    "--topic",
                    topic,
                ]
            )


if __name__ == "__main__":
    main()
