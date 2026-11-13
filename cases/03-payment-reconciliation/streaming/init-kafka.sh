#!/usr/bin/env bash
# Run with the shared Kafka image on its client network. It creates/verifies only a Case 03 topic.
set -euo pipefail

bootstrap=${KAFKA_BOOTSTRAP_SERVERS:-kafka:9092}
topic=${PAYRECON_KAFKA_TOPIC:-case03.payment-events.v1}
if [[ ! "$topic" =~ ^case03\.[a-zA-Z0-9._-]+$ || ${#topic} -gt 249 ]]; then
    echo "Refusing a topic outside the case03. namespace or Kafka's length limit" >&2
    exit 2
fi

configs=(
    cleanup.policy=delete
    retention.ms=604800000
    retention.bytes=536870912
    segment.bytes=67108864
    segment.ms=3600000
    message.timestamp.type=LogAppendTime
)
args=()
for setting in "${configs[@]}"; do
    args+=(--config "$setting")
done

/opt/kafka/bin/kafka-topics.sh --bootstrap-server "$bootstrap" --create --if-not-exists \
    --topic "$topic" --partitions 3 --replication-factor 1 "${args[@]}"
description=$(/opt/kafka/bin/kafka-topics.sh --bootstrap-server "$bootstrap" --describe --topic "$topic")
if [[ ! "$description" =~ PartitionCount:[[:space:]]+3[[:space:]] || \
      ! "$description" =~ ReplicationFactor:[[:space:]]+1[[:space:]] ]]; then
    echo "Existing topic shape differs; no automatic partition or replication change: $topic" >&2
    exit 1
fi
for setting in "${configs[@]}"; do
    # Fixed settings contain no regex syntax except dots, which must be literal.
    pattern=${setting//./\\.}
    if ! grep -Eq "(^|[[:space:],])${pattern}([[:space:],]|$)" <<< "$description"; then
        echo "Existing topic configuration differs ($setting); no settings altered: $topic" >&2
        exit 1
    fi
done
echo "$description"
echo "Verified Case 03 topic: $topic"
