"""A12: one continuous query, repeated event-time cohorts, duplicate/late guards and durable-history growth."""

import json
import subprocess
import time
import uuid
from datetime import datetime, timedelta

from check_intake import CASE, COMPOSE, PLATFORM, run


def main():
    token = uuid.uuid4().hex[:12]
    topic, database = f"case03.live-check-{token}", f"payments_stream_check_{token}"
    checkpoint = f"/state/check-{token}"
    name = f"case03-state-{token}"
    target = {str(p): 0 for p in range(3)}
    evidence = CASE / "evidence/ci"
    evidence.mkdir(parents=True, exist_ok=True)
    log = evidence / f"state-live-{token}.log"
    process = None
    handle = None

    def stream(*args, python=False):
        command = COMPOSE + ["run", "--rm", "-T", "-e", f"PGDATABASE={database}"]
        if python:
            command += ["--entrypoint", "python"]
        return run(command + ["stream", *args]).stdout

    def probe():
        return json.loads(stream("/app/streaming/probe_state.py", python=True))

    def publish(cohort):
        result = json.loads(stream("stream-produce-state-fixture", "--topic", topic, "--cohort", str(cohort)))
        assert result["acknowledged"] == 48, result
        for position in result["offsets"]:
            p = str(position["partition"])
            target[p] = max(target[p], position["offset"] + 1)

    def settle(*, watermark=None, duplicates=None, drops=None):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(
                    f"continuous live consumer exited {process.returncode}: {log.read_text(encoding='utf-8')[-5000:]}"
                )
            result = probe()
            if (
                result["ready"]
                and result["frontier"] == {topic: target}
                and (watermark is None or result["watermark"] >= watermark)
                and (duplicates is None or result["duplicate_total"] >= duplicates)
                and (drops is None or result["dropped_total"] >= drops)
            ):
                return result
            time.sleep(1)
        raise RuntimeError(f"live query did not reach target {target}: {result}")

    def running():
        return run(["docker", "inspect", "--format", "{{.State.Running}}", name]).stdout.strip() == "true"

    def stop_live():
        if process is None:
            return
        label = run(
            ["docker", "inspect", "--format", '{{index .Config.Labels "payrecon.state-check"}}', name],
            check=False,
        )
        if label.returncode == 0 and label.stdout.strip() == token:
            run(["docker", "stop", "--signal", "SIGINT", "--timeout", "30", name])
        code = process.wait(timeout=45)
        if code not in (0, 130):
            raise RuntimeError(
                f"live consumer did not stop cleanly ({code}): {log.read_text(encoding='utf-8')[-5000:]}"
            )

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
        if run(["docker", "inspect", name], check=False).returncode == 0:
            raise RuntimeError("unique check container already exists; refusing to replace it")
        handle = log.open("w", encoding="utf-8")
        command = COMPOSE + [
            "run",
            "--rm",
            "-T",
            "--name",
            name,
            "--label",
            f"payrecon.state-check={token}",
            "-e",
            f"PGDATABASE={database}",
            "stream",
            "stream-live",
            "--topic",
            topic,
            "--checkpoint",
            checkpoint + "/live",
        ]
        process = subprocess.Popen(
            command,
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        observations = []
        for cohort in range(8):
            publish(cohort)
            # Cohort event times span 48 seconds; wait for the cleanup batch's actual watermark.
            expected = (
                datetime(2026, 10, 15, 10) + timedelta(minutes=7 * cohort - 10, seconds=47)
            ).isoformat() + ".000Z"
            result = settle(watermark=expected)
            assert running(), result
            if observations:
                assert (
                    result["query_id"] == observations[0]["query_id"]
                    and result["run_id"] == observations[0]["run_id"]
                ), result
                assert result["watermark"] >= observations[-1]["watermark"], result
            assert result["business"]["stream.live_event"]["count"] == 48 * (cohort + 1), result
            assert result["business"]["stream.payment_latest"]["count"] == 24 * (cohort + 1), result
            assert result["rows"] <= 4 * 48 and result["max_observed_rows"] <= 5 * 48, result
            if cohort >= 3:
                assert result["removed_total"] - observations[-1]["removed_total"] >= 48, result
            observations.append(result)
            print(
                f"PASS: cohort {cohort}: ledger={48 * (cohort + 1)}, state rows={result['rows']}, removed={result['removed_total']}, bytes={result['memory_bytes']}, watermark={result['watermark']}",
                flush=True,
            )
        warm = observations[-1]
        # Observe more than two five-second triggers without fabricating newer event time.
        time.sleep(12)
        idle = probe()
        assert running() and idle["rows"] == warm["rows"] and idle["watermark"] == warm["watermark"], idle
        assert idle["run_id"] == warm["run_id"], idle
        publish(7)
        duplicate = settle(duplicates=warm["duplicate_total"] + 48)
        assert duplicate["business"] == warm["business"], "within-window resend changed business rows"
        publish(0)
        late = settle(drops=duplicate["dropped_total"] + 48)
        assert late["business"] == warm["business"], (
            "post-eviction resend inflated the ledger or minute totals"
        )
        assert late["rows"] <= 4 * 48, late
        assert [float(x) for x in late["counts"]] == [8, 144, 48, 0, 25, 25], late
        stop_live()
        stopped = probe()
        stream("stream-intake", "--topic", topic, "--checkpoint", checkpoint + "/intake", "--available-now")
        durable = probe()
        assert durable["deliveries"] == 480 and durable["intake_events"] == 384, durable
        assert durable["business"] == stopped["business"], durable
        generation = durable["generation"]
        state = json.loads(stream("stream-status", "--generation", generation))
        assert state["consistent_at_recorded_frontier"], state
        stream("stream-live", "--topic", topic, "--checkpoint", checkpoint + "/live", "--available-now")
        resumed = probe()
        assert resumed["query_id"] == warm["query_id"] and resumed["business"] == warm["business"], resumed
        report = dict(
            profile=dict(
                cohorts=8,
                payments_per_cohort=24,
                events_per_cohort=48,
                stride_minutes=7,
                watermark_minutes=10,
                steady_row_guard=192,
                transient_row_guard=240,
            ),
            cohorts=observations,
            idle=idle,
            duplicate=duplicate,
            post_eviction_resend=late,
            durable=durable,
            resumed=resumed,
            status=state,
        )
        (evidence / "state-integration.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(
            "PASS: one continuous query evicts repeatedly; duplicates and idle period preserve business totals",
            flush=True,
        )
        print(
            "PASS: 480 deliveries/384 canonical events retained in intake; 384 live events and checkpoint survive state eviction/restart",
            flush=True,
        )
        print("State integration checks passed", flush=True)
    finally:
        try:
            if process is not None and process.poll() is None:
                stop_live()
        finally:
            if handle is not None:
                handle.close()
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
