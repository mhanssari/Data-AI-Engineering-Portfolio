"""Unwatermarked Kafka -> transactional PostgreSQL intake, with persistent lineage/checkpoints."""

import json
from datetime import UTC
from itertools import islice
from pathlib import Path

from payrecon import db
from payrecon.streaming.contract import Dataset
from payrecon.streaming.intake import Delivery, halt_on_conflict, persist_batch, require_stream_database
from payrecon.streaming.lineage import register
from payrecon.streaming.producer import bootstrap, identity


def run_intake(dataset: Dataset, topic: str, checkpoint: Path, *, available_now: bool = False) -> None:
    from pyspark.sql import SparkSession

    source = identity(topic)
    jars = sorted(Path("/opt/ivy/jars").glob("*.jar"))
    if not jars:
        raise RuntimeError("connector jars are missing; use the case's streaming Docker image")
    spark = None
    with db.connect() as con:
        require_stream_database(con)
        locked = con.execute(
            "select pg_try_advisory_lock(hashtextextended(%s,0))", ["payrecon/intake/" + dataset.dataset_id]
        ).fetchone()[0]
        if not locked:
            raise RuntimeError("another intake writer owns this dataset")
        generation = register(con, dataset, source, checkpoint)
        try:
            spark = (
                SparkSession.builder.master("local[2]")
                .appName("case03-intake")
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
                rows = list(islice(frame.toLocalIterator(), 5001))
                if (
                    len(rows) > 5000
                    or sum(len(r.value or b"") + len(r.key or b"") for r in rows) > 16 * 1024 * 1024
                ):
                    raise RuntimeError("intake batch exceeds the local row/byte budget; no rows committed")
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
                summary, reused = persist_batch(con, dataset, source, generation, batch_id, records)
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
                .option("groupIdPrefix", f"case03-intake-{generation}")
                .load()
            )
            writer = (
                frame.select("partition", "offset", "key", "value", "timestamp")
                .writeStream.queryName("case03-intake")
                .option("checkpointLocation", str(checkpoint.resolve()))
                .foreachBatch(write_batch)
            )
            writer = (
                writer.trigger(availableNow=True)
                if available_now
                else writer.trigger(processingTime="5 seconds")
            )
            query = writer.start()
            query.awaitTermination()
            print(
                json.dumps(
                    dict(generation=generation, status="completed", progress=query.lastProgress), default=str
                ),
                flush=True,
            )
        finally:
            if spark:
                spark.stop()
