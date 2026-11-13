"""Shared Kafka integration check. Python stdlib only; all producer/consumer tools run in Linux containers.

Requires the stack already up and --restart to acknowledge the disruptive platform restart. It creates unique
test topics, never changes the real case topic, and removes only its own test topics after success or failure.
The host probe uses Kafka Metadata v4, not a TCP-only check; producer/consumer roundtrips use official Java tools.
"""

import argparse
import io
import json
import re
import socket
import struct
import subprocess
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ["docker", "compose", "-f", str(ROOT / "platform/kafka/compose.yaml")]


def run(args: list[str], data: str | None = None, *, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(args, input=data, text=True, capture_output=True, timeout=90)
    if check and result.returncode:
        raise RuntimeError(f"command failed: {args}\n{result.stdout}\n{result.stderr}")
    return result


def admin(tool: str, *args: str) -> str:
    return run(COMPOSE + ["exec", "-T", "kafka", f"/opt/kafka/bin/{tool}.sh", *args]).stdout


def snapshot(topic: str) -> dict:
    text = admin("kafka-topics", "--bootstrap-server", "kafka:9092", "--describe", "--topic", topic)
    return {
        "topic_id": re.search(r"TopicId:\s+(\S+)", text)[1],
        "partitions": int(re.search(r"PartitionCount:\s+(\d+)", text)[1]),
        "replicas": int(re.search(r"ReplicationFactor:\s+(\d+)", text)[1]),
        "configs": dict(
            item.split("=", 1) for item in re.search(r"Configs:\s*([^\n]*)", text)[1].split(",") if item
        ),
    }


def host_metadata() -> dict:
    # Metadata request v4 has a non-flexible header and no topics, with auto-creation explicitly false.
    client_id = b"portfolio-host-probe"
    request = struct.pack(">hhi", 3, 4, 3303) + struct.pack(">h", len(client_id)) + client_id
    request += struct.pack(">i?", 0, False)

    def read_exact(connection, count):
        chunks = bytearray()
        while len(chunks) < count:
            chunk = connection.recv(count - len(chunks))
            if not chunk:
                raise RuntimeError("broker closed the metadata response")
            chunks.extend(chunk)
        return bytes(chunks)

    with socket.create_connection(("127.0.0.1", 19092), timeout=10) as connection:
        connection.sendall(struct.pack(">i", len(request)) + request)
        length = struct.unpack(">i", read_exact(connection, 4))[0]
        if not 0 < length < 1024 * 1024:
            raise RuntimeError(f"invalid metadata response length: {length}")
        response = io.BytesIO(read_exact(connection, length))

    def number(fmt):
        return struct.unpack(fmt, response.read(struct.calcsize(fmt)))[0]

    def string():
        size = number(">h")
        return None if size == -1 else response.read(size).decode("utf-8")

    assert number(">i") == 3303, "wrong correlation ID"
    number(">i")  # throttle time
    brokers = []
    for _ in range(number(">i")):
        node, host, port = number(">i"), string(), number(">i")
        string()  # nullable rack
        brokers.append({"node": node, "host": host, "port": port})
    cluster = string()
    assert brokers == [{"node": 1, "host": "localhost", "port": 19092}], brokers
    assert cluster, "missing cluster identity"
    return {"cluster_id": cluster, "brokers": brokers}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restart", action="store_true", help="acknowledge that the shared broker restarts")
    args = parser.parse_args()
    if not args.restart:
        parser.error("--restart is required; run only when other cases are not using the shared broker")

    config = json.loads(run(COMPOSE + ["config", "--format", "json"]).stdout)
    image = config["services"]["kafka"]["image"]
    network = config["networks"]["clients"]["name"]
    broker_id = run(COMPOSE + ["ps", "-q", "kafka"]).stdout.strip()
    assert broker_id, "start the shared broker first"
    token = uuid.uuid4().hex[:12]
    case_topic = f"case03.check-{token}"
    sentinel = f"platform.sentinel-{token}"
    host_topic = f"platform.host-check-{token}"
    topics = [case_topic, sentinel, host_topic]
    existing = set(admin("kafka-topics", "--bootstrap-server", "kafka:9092", "--list").splitlines())
    assert not existing.intersection(topics), "test topic collision; nothing was changed"

    def client(tool: str, *tool_args: str, host: bool = False, data: str | None = None) -> str:
        route = f"container:{broker_id}" if host else network
        return run(
            [
                "docker",
                "run",
                "--rm",
                *(["-i"] if data is not None else []),
                "--network",
                route,
                "--entrypoint",
                f"/opt/kafka/bin/{tool}.sh",
                image,
                *tool_args,
            ],
            data,
        ).stdout

    def initialise(topic: str = case_topic) -> subprocess.CompletedProcess:
        return run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                network,
                "-e",
                f"PAYRECON_KAFKA_TOPIC={topic}",
                "-v",
                f"{ROOT / 'cases/03-payment-reconciliation/streaming'}:/case:ro",
                "--entrypoint",
                "bash",
                image,
                "/case/init-kafka.sh",
            ],
            check=False,
        )

    def produce(topic: str, message: str, *, host: bool = False) -> None:
        client(
            "kafka-console-producer",
            "--bootstrap-server",
            "localhost:19092" if host else "kafka:9092",
            "--topic",
            topic,
            "--producer-property",
            "acks=all",
            "--producer-property",
            "delivery.timeout.ms=30000",
            "--producer-property",
            "request.timeout.ms=10000",
            data=message + "\n",
            host=host,
        )

    def consume(topic: str, expected: str, *, host: bool = False) -> None:
        text = client(
            "kafka-console-consumer",
            "--bootstrap-server",
            "localhost:19092" if host else "kafka:9092",
            "--topic",
            topic,
            "--partition",
            "0",
            "--offset",
            "0",
            "--max-messages",
            "1",
            "--timeout-ms",
            "20000",
            "--formatter-property",
            "print.timestamp=true",
            host=host,
        )
        assert expected in text and "LogAppendTime:" in text, text

    created_topics = []
    try:
        for topic in (sentinel, host_topic):
            admin(
                "kafka-topics",
                "--bootstrap-server",
                "kafka:9092",
                "--create",
                "--topic",
                topic,
                "--partitions",
                "1",
                "--replication-factor",
                "1",
                "--config",
                "message.timestamp.type=LogAppendTime",
            )
            created_topics.append(topic)
        produce(sentinel, f"sentinel-{token}")
        before_sentinel = snapshot(sentinel)
        created_topics.append(case_topic)
        first = initialise()
        assert first.returncode == 0, first.stderr + first.stdout
        before_case = snapshot(case_topic)
        assert before_case["partitions"] == 3 and before_case["replicas"] == 1
        again = initialise()
        assert again.returncode == 0, again.stderr + again.stdout
        assert snapshot(case_topic) == before_case, "repeat initialisation changed the topic"
        assert snapshot(sentinel) == before_sentinel, "case setup changed the unrelated topic"
        outside_case = initialise(sentinel)
        assert outside_case.returncode == 2, outside_case.stdout + outside_case.stderr
        assert snapshot(sentinel) == before_sentinel, "namespace rejection changed the unrelated topic"
        print("PASS: case topic initialisation is repeatable; unrelated topic unchanged", flush=True)

        # Make retention longer on this test topic, then prove initialisation refuses to silently reset it.
        admin(
            "kafka-configs",
            "--bootstrap-server",
            "kafka:9092",
            "--alter",
            "--entity-type",
            "topics",
            "--entity-name",
            case_topic,
            "--add-config",
            "retention.ms=691200000",
        )
        drift = initialise()
        assert drift.returncode != 0 and "configuration differs" in drift.stderr, drift.stdout + drift.stderr
        assert snapshot(case_topic)["configs"]["retention.ms"] == "691200000"
        admin(
            "kafka-configs",
            "--bootstrap-server",
            "kafka:9092",
            "--alter",
            "--entity-type",
            "topics",
            "--entity-name",
            case_topic,
            "--add-config",
            "retention.ms=604800000",
        )
        assert initialise().returncode == 0
        print("PASS: configuration drift fails without silently changing the existing topic", flush=True)

        produce(host_topic, f"host-{token}", host=True)
        consume(host_topic, f"host-{token}", host=True)
        consume(sentinel, f"sentinel-{token}")
        before_host = host_metadata()
        print(f"PASS: host metadata from the real host: {json.dumps(before_host)}", flush=True)
        print(
            "PASS: internal client and HOST-listener container roundtrips; broker append timestamps",
            flush=True,
        )

        run(COMPOSE + ["restart", "kafka"])
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            result = run(
                COMPOSE
                + [
                    "exec",
                    "-T",
                    "kafka",
                    "/opt/kafka/bin/kafka-topics.sh",
                    "--bootstrap-server",
                    "kafka:9092",
                    "--list",
                ],
                check=False,
            )
            if result.returncode == 0:
                break
            time.sleep(2)
        else:
            raise RuntimeError("broker failed to recover after restart")
        assert snapshot(case_topic) == before_case, "case topic identity/configuration changed on restart"
        assert snapshot(sentinel) == before_sentinel, "unrelated topic changed on restart"
        consume(sentinel, f"sentinel-{token}")
        consume(host_topic, f"host-{token}", host=True)
        assert host_metadata() == before_host, "cluster/listener identity changed on restart"
        print("PASS: restart preserves cluster/topic identities, settings and both test messages", flush=True)
        print("Kafka foundation checks passed", flush=True)
    finally:
        for topic in created_topics:
            cleanup = run(
                COMPOSE
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
                ],
                check=False,
            )
            if cleanup.returncode:
                raise RuntimeError(f"test topic cleanup failed for {topic}: {cleanup.stderr}")


if __name__ == "__main__":
    main()
