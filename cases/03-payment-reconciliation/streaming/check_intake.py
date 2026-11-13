"""Run a tiny real producer -> Spark -> PostgreSQL check. Host Python stdlib and Docker only.

The shared broker and the case PostgreSQL must already be healthy, and the optional image built. A unique test
topic/database/checkpoint isolates this check; cleanup never removes shared services or their volumes.
"""

import json
import subprocess
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CASE = ROOT / "cases/03-payment-reconciliation"
COMPOSE = ["docker", "compose", "-f", str(CASE / "compose.yaml"), "-f", str(CASE / "streaming/compose.yaml")]
PLATFORM = ["docker", "compose", "-f", str(ROOT / "platform/kafka/compose.yaml")]


def run(args, *, check=True):
    result = subprocess.run(args, text=True, capture_output=True, timeout=240)
    if check and result.returncode:
        raise RuntimeError(f"command failed: {args}\n{result.stdout}\n{result.stderr}")
    return result


def main():
    token = uuid.uuid4().hex[:12]
    topic, database = f"case03.intake-check-{token}", f"payments_stream_check_{token}"
    checkpoint = f"/state/check-{token}"

    def stream(*args, python=False):
        command = COMPOSE + ["run", "--rm", "-T", "-e", f"PGDATABASE={database}"]
        if python:
            command += ["--entrypoint", "python"]
        return run(command + ["stream", *args]).stdout

    def snapshot():
        return json.loads(stream("/app/streaming/probe_intake.py", python=True))

    def drain():
        stream("stream-intake", "--topic", topic, "--checkpoint", checkpoint, "--available-now")

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
        acknowledgements = json.loads(stream("stream-produce-fixture", "--topic", topic))
        assert acknowledgements["acknowledged"] == 8
        drain()
        first = snapshot()
        assert first["stream.delivery"] == 8 and first["stream.intake_event"] == 3, first
        assert first["stream.quarantine"] == 4 and first["blocking"] == 0 and first["late_events"] == 1, first
        print(f"PASS: first drain archives poison and the day-late event: {json.dumps(first)}", flush=True)
        drain()
        assert snapshot() == first, "checkpoint restart changed durable events or first-commit metadata"
        print(
            "PASS: restart with the same checkpoint changes no delivery, event or quarantine row", flush=True
        )
        stream("stream-produce-fixture", "--topic", topic)
        drain()
        second = snapshot()
        assert second["stream.delivery"] == 16 and second["stream.quarantine"] == 8, second
        assert (
            second["stream.intake_event"] == 3 and second["canonical_digest"] == first["canonical_digest"]
        ), second
        assert second["stream.lineage"] == 1 and second["late_events"] == 1 and second["blocking"] == 0, (
            second
        )
        print(
            f"PASS: republished events add transport evidence without canonical duplicates: {json.dumps(second)}",
            flush=True,
        )
        print("Intake integration checks passed", flush=True)
    finally:
        if prepared:
            stream("/app/streaming/probe_intake.py", "--cleanup-token", token, python=True)
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
