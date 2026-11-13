"""Actual abrupt consumer exits, checkpoint replay and controlled late repair. Host stdlib/Docker only."""

import json
import uuid

from check_intake import CASE, COMPOSE, PLATFORM, run


def main():
    token = uuid.uuid4().hex[:12]
    topic, database = f"case03.live-check-{token}", f"payments_stream_check_{token}"
    checkpoint = f"/state/check-{token}"
    live_path = checkpoint + "/live"
    plan_file = CASE / f"evidence/ci/recovery-plan-{token}.json"

    def stream(*args, python=False, check=True):
        command = COMPOSE + ["run", "--rm", "-T", "-e", f"PGDATABASE={database}"]
        if python:
            command += ["--entrypoint", "python"]
        return run(command + ["stream", *args], check=check)

    def drain(role, *extra, check=True):
        return stream(
            f"stream-{role}",
            "--topic",
            topic,
            "--checkpoint",
            f"{checkpoint}/{role}",
            "--available-now",
            *extra,
            check=check,
        )

    def publish(phase, count):
        result = json.loads(stream("stream-produce-live-fixture", "--topic", topic, "--phase", phase).stdout)
        assert result["acknowledged"] == count, result
        drain("intake")

    def probe(*extra):
        return json.loads(
            stream("/app/streaming/probe_recovery.py", "--checkpoint", live_path, *extra, python=True).stdout
        )

    def fail(point, expected_code):
        result = drain("live", "--crash-at", point, check=False)
        assert result.returncode == expected_code, (result.returncode, result.stdout, result.stderr)
        marker = next(
            json.loads(line) for line in result.stdout.splitlines() if line.startswith('{"diagnostic_crash"')
        )
        assert marker["diagnostic_crash"] == point
        snapshot = probe()
        assert marker["batch_id"] in snapshot["offsets"] and marker["batch_id"] not in snapshot["commits"], (
            snapshot
        )
        return marker, snapshot

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
        stream("stream-reference", "--arrival", "2026-10-12")
        publish("initial", 14)
        before_marker, before = fail("before-commit", 86)
        assert all(v["count"] == 0 for v in before["business"].values()), before
        assert not before["batches"], before
        retry = drain("live")
        recovered = probe()
        assert recovered["state"]["generation"] == before_marker["generation"], recovered
        assert recovered["business"]["stream.live_event"]["count"] == 11, recovered
        assert (
            recovered["query_id"] == before["query_id"] and before_marker["batch_id"] in recovered["commits"]
        ), recovered
        print(
            "PASS: abrupt exit before DB commit rolls back all business rows; same checkpoint/generation recovers 11 events",
            flush=True,
        )
        publish("advance", 1)
        after_marker, after = fail("after-commit", 87)
        assert after["business"]["stream.live_event"]["count"] == 12, after
        committed = next(b for b in after["batches"] if b["batch_id"] == after_marker["batch_id"])
        retry = drain("live")
        assert any(
            json.loads(line).get("reused") is True
            for line in retry.stdout.splitlines()
            if line.startswith("{")
        ), retry.stdout
        replayed = probe()
        assert replayed["business"] == after["business"], replayed
        assert (
            next(b for b in replayed["batches"] if b["batch_id"] == after_marker["batch_id"]) == committed
        ), replayed
        assert (
            replayed["query_id"] == before["query_id"] and after_marker["batch_id"] in replayed["commits"]
        ), replayed
        print(
            "PASS: abrupt exit after DB commit leaves Spark batch uncommitted; replay reuses DB commit without changing any business row",
            flush=True,
        )
        publish("late-gap", 1)
        drain("live")
        incomplete = probe()
        assert incomplete["state"]["intake_events_not_admitted"] == 1, incomplete
        assert not incomplete["state"]["consistent_at_recorded_frontier"], incomplete
        generation = incomplete["state"]["generation"]
        spec = json.loads(stream("stream-backfill-plan", "--generation", generation).stdout)
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        repair_args = [
            "stream-backfill",
            "--plan",
            f"/app/evidence/ci/{plan_file.name}",
            "--repair-id",
            f"recovery-{token}",
            "--reason",
            "Recover the deliberately late authorisation at the declared intake/reference frontier",
        ]
        repaired = json.loads(stream(*repair_args).stdout)
        assert repaired["added_events"] == 1 and not repaired["reused"], repaired
        final = probe("--parity")
        assert final["checkpoint_hash"] == incomplete["checkpoint_hash"], (
            "backfill changed Spark checkpoint/sidecar"
        )
        assert (
            final["state"]["consistent_at_recorded_frontier"]
            and final["business"]["stream.live_event"]["count"] == 13
        ), final
        assert final["batch_parity"] == dict(
            payments=6, applied_events=12, minute_groups=8, illegal_events=1
        ), final
        repeated = json.loads(stream(*repair_args).stdout)
        assert repeated["reused"] and probe()["business"] == final["business"], repeated
        drain("live")
        resumed = probe()
        assert resumed["query_id"] == before["query_id"] and resumed["business"] == final["business"], resumed
        print(
            "PASS: controlled late repair adds one authorisation, matches independent batch state/minutes, repeats safely and resumes the same checkpoint",
            flush=True,
        )
        publish("late-gap", 1)
        drain("live")
        resent = probe()
        assert (
            resent["business"] == final["business"] and resent["state"]["consistent_at_recorded_frontier"]
        ), resent
        report = dict(
            before_commit=dict(marker=before_marker, recovered=recovered),
            after_commit=dict(marker=after_marker, replayed=replayed),
            plan=spec,
            repair=repaired,
            final=final,
            resend=resent,
        )
        destination = CASE / "evidence/ci/recovery-integration.json"
        destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(
            "PASS: later re-delivery of the backfilled event changes no canonical row or minute total",
            flush=True,
        )
        print("Recovery integration checks passed", flush=True)
    finally:
        plan_file.unlink(missing_ok=True)
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
