"""Three finite current-clock profiles. Host stdlib only; shared healthy Kafka/PostgreSQL required."""

import json
import subprocess
import time
import uuid
from datetime import UTC, datetime, timedelta

from check_intake import CASE, COMPOSE, PLATFORM, run


def repeat(number):
    token = uuid.uuid4().hex[:12]
    topic, database = f"case03.live-check-{token}", f"payments_stream_check_{token}"
    checkpoint, name = f"/state/check-{token}", f"case03-measure-{token}"
    evidence = CASE / "evidence/ci"
    evidence.mkdir(parents=True, exist_ok=True)
    log = evidence / f"measurement-live-{token}.log"
    start = datetime.now(UTC)
    clocks = [
        "--source-start",
        (start - timedelta(minutes=5)).isoformat(),
        "--source-end",
        (start + timedelta(minutes=30)).isoformat(),
    ]
    process = handle = None
    prepared = False

    def stream(*args, python=False):
        command = COMPOSE + ["run", "--rm", "-T", "-e", f"PGDATABASE={database}"]
        if python:
            command += ["--entrypoint", "python"]
        return run(command + ["stream", *args]).stdout

    def stop():
        label = run(
            ["docker", "inspect", "--format", '{{index .Config.Labels "payrecon.measurement"}}', name],
            check=False,
        )
        if label.returncode == 0 and label.stdout.strip() == token:
            run(["docker", "stop", "--signal", "SIGINT", "--timeout", "30", name])
        code = process.wait(timeout=45)
        if code not in (0, 130):
            raise RuntimeError(f"live consumer exit {code}: {log.read_text(encoding='utf-8')[-5000:]}")

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
            raise RuntimeError("unique measurement container already exists")
        handle = log.open("w", encoding="utf-8")
        command = COMPOSE + [
            "run",
            "--rm",
            "-T",
            "--name",
            name,
            "--label",
            f"payrecon.measurement={token}",
            "-e",
            f"PGDATABASE={database}",
            "stream",
            "stream-live",
            "--topic",
            topic,
            "--checkpoint",
            checkpoint + "/live",
            *clocks,
        ]
        process = subprocess.Popen(
            command,
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        print(
            f"Run {number}: warming a new JVM/query, then 600 measured events at 10 events/s; token={token}",
            flush=True,
        )
        load = stream("/app/streaming/measure_load.py", "load", "--token", token, python=True)
        print(load, flush=True)
        if process.poll() is not None:
            raise RuntimeError(
                f"live query exited during measurement: {log.read_text(encoding='utf-8')[-5000:]}"
            )
        deadline = time.monotonic() + 60
        while True:
            probe = json.loads(stream("/app/streaming/probe_state.py", python=True))
            if probe["ready"] and sum(probe["frontier"][topic].values()) == 800:
                break
            if time.monotonic() > deadline or process.poll() is not None:
                raise RuntimeError(f"live progress did not reach all 800 acknowledgements: {probe}")
            time.sleep(1)
        stop()
        stream(
            "stream-intake",
            "--topic",
            topic,
            "--checkpoint",
            checkpoint + "/intake",
            "--available-now",
            *clocks,
        )
        report = json.loads(stream("/app/streaming/measure_load.py", "finish", "--token", token, python=True))
        probe = json.loads(stream("/app/streaming/probe_state.py", python=True))
        assert probe["business"]["stream.live_event"]["count"] == 800, probe
        assert probe["business"]["stream.payment_latest"]["count"] == 400, probe
        assert [float(v) for v in probe["counts"]][1:4] == [300, 100, 0], probe
        status = json.loads(stream("stream-status", "--generation", probe["generation"]))
        assert status["consistent_at_recorded_frontier"], status
        report.update(probe=probe, status=status)
        (evidence / f"measurement-{number}.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(
            f"PASS run {number}: {json.dumps(report['summary'])}; 800 raw/canonical/live, 400 payments, consistent frontier",
            flush=True,
        )
        return {k: v for k, v in report.items() if k not in ("records", "backlog", "clock_checks")}
    finally:
        try:
            if process is not None and process.poll() is None:
                stop()
        finally:
            if handle:
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


def main():
    reports = []
    for number in range(1, 4):
        reports.append(repeat(number))
        (CASE / "evidence/ci/measurement-summary.json").write_text(
            json.dumps(reports, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
