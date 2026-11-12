# Case 03 streaming infrastructure

The [streaming design](../docs/streaming-design.md) separates durable intake and the bounded live view. This slice implements only the shared broker prerequisite and this case's topic initializer. No versioned payment producer, Spark job, intake migration or operational stream view exists yet.

Start the [shared Kafka](../../../platform/kafka/README.md), then run this directory's `init-kafka.sh` through its optional tools container, as documented there. The script is authoritative for the case topic's configuration; platform startup does not create or modify case topics.

- Topic: `case03.payment-events.v1`, three partitions, replication factor 1.
- Source key contract: payment ID. Future Spark readers have separate case-prefixed query groups and checkpoints.
- `PAYRECON_KAFKA_TOPIC` can select another name under `case03.` for isolated testing; other namespaces are refused.
- Existing topics must match all expected settings. A mismatch fails and requires a deliberate review; startup never repairs settings or deletes/recreates a topic automatically.
- Producer/consumer safety, stream epoch registration and the agreed ten-minute watermark remain future implementation work. Kafka topic persistence is not evidence of Spark checkpoint recovery.

Keep Airflow stopped during local streaming checks to preserve the laptop's memory headroom. Kafka shutdown/storage belongs to the platform; this case must not delete the shared broker volume or another case's topics.
