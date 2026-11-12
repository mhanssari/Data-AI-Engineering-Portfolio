"""Unwatermarked Kafka -> transactional PostgreSQL intake, with persistent lineage/checkpoints."""

import json
from datetime import UTC
from itertools import islice
from pathlib import Path

from payrecon import db
from payrecon.streaming.contract import ContractError, Dataset, decode, utc
from payrecon.streaming.faults import crash, validate_crash
from payrecon.streaming.intake import Delivery, halt_on_conflict, persist_batch, require_stream_database
from payrecon.streaming.lineage import register
from payrecon.streaming.live import lock_writer, persist_live
from payrecon.streaming.producer import bootstrap, identity
from payrecon.streaming.progress import record_progress


def run_intake(dataset: Dataset, topic: str, checkpoint: Path, *, available_now: bool = False) -> None:
    run_query(dataset, topic, checkpoint, role="intake", available_now=available_now)


def live_columns(frame, dataset: Dataset):
    """Strict contract validation runs before event time can reach the watermark operator."""
    from pyspark.sql import functions as F
    from pyspark.sql.types import StringType, StructField, StructType, TimestampType

    schema = StructType([StructField("event_id", StringType()), StructField("event_time", TimestampType())])

    @F.udf(returnType=schema, useArrow=False)
    def validated(key, value):
        try:
            event, _ = decode(
                bytes(key) if key is not None else None, bytes(value) if value is not None else None, dataset
            )
            return event["event_id"], utc(event["event_time"])
        except ContractError:
            return None

    return (
        frame.withColumn("validated", validated("key", "value"))
        .where(F.col("validated").isNotNull())
        .select("partition", "offset", "key", "value", "timestamp", "validated.*")
        .withWatermark("event_time", "10 minutes")
        .dropDuplicatesWithinWatermark(["event_id"])
    )


def run_query(
    dataset: Dataset,
    topic: str,
    checkpoint: Path,
    *,
    role: str,
    available_now: bool = False,
    crash_at: str | None = None,
) -> None:
    from pyspark.sql import SparkSession

    validate_crash(crash_at, role, topic)
    source = identity(topic)
    jars = sorted(Path("/opt/ivy/jars").glob("*.jar"))
    if not jars:
        raise RuntimeError("connector jars are missing; use the case's streaming Docker image")
    spark = None
    with db.connect() as con:
        require_stream_database(con)
        if role == "live":
            lock_writer(con, session=True)
            if not con.execute("select 1 from stream.reference_snapshot").fetchone():
                raise ValueError("load the independent stream reference snapshot first")
        else:
            locked = con.execute(
                "select pg_try_advisory_lock(hashtextextended(%s,0))",
                ["payrecon/intake/" + dataset.dataset_id],
            ).fetchone()[0]
            if not locked:
                raise RuntimeError("another intake writer owns this dataset")
        generation = register(con, dataset, source, checkpoint, role=role)
        try:
            spark = (
                SparkSession.builder.master("local[2]")
                .appName(f"case03-{role}")
                .config("spark.ui.enabled", "false")
                .config("spark.sql.session.timeZone", "UTC")
                .config("spark.sql.shuffle.partitions", "3")
                .config("spark.driver.memory", "1g")
                .config("spark.driver.host", "127.0.0.1")
                .config("spark.driver.bindAddress", "127.0.0.1")
                .config("spark.jars", ",".join(map(str, jars)))
                .getOrCreate()
            )
            spark.sparkContext.setLogLevel("WARN")

            def write_batch(frame, batch_id):
                # Full bounded consumption precedes the idempotency lookup, including a replayed callback.
                rows, size = [], 0
                for row in islice(frame.toLocalIterator(), 5001):
                    size += len(row.value or b"") + len(row.key or b"")
                    if len(rows) == 5000 or size > 16 * 1024 * 1024:
                        raise RuntimeError(
                            "stream batch exceeds the local row/byte budget; no rows committed"
                        )
                    rows.append(row)
                records = [
                    Delivery(
                        r.partition,
                        r.offset,
                        bytes(r.key) if r.key is not None else None,
                        bytes(r.value) if r.value is not None else None,
                        r.timestamp.replace(tzinfo=UTC) if r.timestamp else None,
                    )
                    for r in rows
                ]
                sink = persist_live if role == "live" else persist_batch
                options = {}
                if records and crash_at == "before-commit":
                    options["before_commit"] = lambda: crash(crash_at, generation, batch_id)
                summary, reused = sink(con, dataset, source, generation, batch_id, records, **options)
                if records and crash_at == "after-commit":
                    crash(crash_at, generation, batch_id)
                print(
                    json.dumps(dict(generation=generation, batch_id=batch_id, reused=reused, **summary)),
                    flush=True,
                )
                # Conflict evidence is committed, but Spark must not advance beyond this blocking batch.
                halt_on_conflict(summary)

            frame = (
                spark.readStream.format("kafka")
                .option("kafka.bootstrap.servers", bootstrap())
                .option("subscribe", topic)
                .option("startingOffsets", "earliest")
                .option("failOnDataLoss", "true")
                .option("maxOffsetsPerTrigger", "3000")
                .option("groupIdPrefix", f"case03-{role}-{generation}")
                .load()
            )
            if role == "live":
                frame = live_columns(frame, dataset)
            writer = (
                frame.select("partition", "offset", "key", "value", "timestamp")
                .writeStream.queryName(f"case03-{role}")
                .option("checkpointLocation", str(checkpoint.resolve()))
                .foreachBatch(write_batch)
            )
            writer = (
                writer.trigger(availableNow=True)
                if available_now
                else writer.trigger(processingTime="5 seconds")
            )
            query = writer.start()
            seen = set()

            # foreachBatch runs on another thread: progress must use a separate DB connection.
            with db.connect() as progress_con:

                def report():
                    for progress in query.recentProgress:
                        batch_id = progress["batchId"]
                        if batch_id not in seen:
                            record_progress(progress_con, generation, role, progress)
                            seen.add(batch_id)

                while not query.awaitTermination(1):
                    report()
                report()
            print(
                json.dumps(
                    dict(generation=generation, status="completed", progress=query.lastProgress), default=str
                ),
                flush=True,
            )
        finally:
            if spark:
                spark.stop()
