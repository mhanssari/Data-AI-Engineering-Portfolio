"""Container-side current-clock producer and independent committed-row observer. Diagnostic databases only."""

import argparse
import json
import os
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from payrecon import db
from payrecon.streaming.contract import encode, stamp, utc
from payrecon.streaming.measurement import distribution, summarise
from payrecon.streaming.producer import DATASET_ID, bootstrap, identity


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("load", "finish"))
    parser.add_argument("--token", required=True)
    args = parser.parse_args()
    token = args.token
    if (
        not re.fullmatch(r"[a-f0-9]{12}", token)
        or os.environ.get("PGDATABASE") != f"payments_stream_check_{token}"
    ):
        raise ValueError("measurement requires its isolated database/token")
    path = Path(f"/state/check-{token}/measurement.json")
    with db.connect() as con:
        if args.mode == "finish":
            report = json.loads(path.read_text())
            deliveries = list(con.execute('select partition,"offset",broker_time,value from stream.delivery'))
            by_coordinate = {(p, o): (t, json.loads(bytes(v))) for p, o, t, v in deliveries}
            assert len(deliveries) == len(report["records"]) == 800, "incomplete/duplicate intake"
            for record in report["records"]:
                broker, envelope = by_coordinate[(record["partition"], record["offset"])]
                assert envelope["event"]["event_id"] == record["event_id"], "coordinate/event mismatch"
                assert envelope["event"]["event_time"] == record["event_time"]
                record["broker_time"] = stamp(broker)
                # All services use the Docker VM clock; allow millisecond broker precision only.
                assert (broker - utc(envelope["published_at"])).total_seconds() >= -0.002
            report["summary"] = summarise(report["records"])
            report["intake_count"] = len(deliveries)
            report["canonical_count"] = con.execute("select count(*) from stream.intake_event").fetchone()[0]
            assert report["canonical_count"] == 800
            print(json.dumps(report, sort_keys=True))
            return

        from confluent_kafka import Producer

        topic = f"case03.live-check-{token}"
        source = identity(topic)
        producer = Producer(
            {
                "bootstrap.servers": bootstrap(),
                "enable.idempotence": True,
                "acks": "all",
                "delivery.timeout.ms": 30000,
                "request.timeout.ms": 10000,
            }
        )
        records, errors, poll_times, backlog, clock_checks = [], [], [], [], []
        by_id = {}
        next_poll = 0.0

        def observe(force=False):
            nonlocal next_poll
            producer.poll(0)
            now = time.monotonic()
            if not force and now < next_poll:
                return
            start = datetime.now(UTC)
            server = con.execute("select clock_timestamp()").fetchone()[0]
            end = datetime.now(UTC)
            if not start - timedelta(milliseconds=2) <= server <= end + timedelta(milliseconds=2):
                raise RuntimeError(
                    "database and producer clocks differ beyond the measured round-trip bracket"
                )
            clock_checks.append(
                dict(client_before=stamp(start), server=stamp(server), client_after=stamp(end))
            )
            for event_id, observed in con.execute(
                "select e.event_id,clock_timestamp() from stream.live_event e join stream.event_application a "
                "using(generation,event_id) where a.outcome='applied'"
            ):
                record = by_id[event_id]
                record.setdefault("observed_at", stamp(observed))
            poll_times.append(time.monotonic())
            backlog.append(
                dict(
                    at=stamp(server),
                    submitted=len(records),
                    not_observed=sum("observed_at" not in r for r in records),
                )
            )
            next_poll = time.monotonic() + 1.0

        def drain():
            deadline = time.monotonic() + 150
            while any("observed_at" not in r or "offset" not in r for r in records):
                observe()
                if errors or time.monotonic() > deadline:
                    raise RuntimeError(f"load/visibility incomplete: {errors}, {backlog[-1:]}")
                time.sleep(0.02)

        phases = []
        for phase, payments in (("warmup", 100), ("measured", 300)):
            phase_start, next_due = time.monotonic(), time.monotonic()
            lags = []
            for payment in range(payments):
                while time.monotonic() < next_due:
                    observe()
                    time.sleep(min(0.01, max(0, next_due - time.monotonic())))
                lags.append(time.monotonic() - next_due)
                # Do not catch up by bursting if the producer/observer falls behind.
                next_due = time.monotonic() + 0.2
                emitted = datetime.now(UTC)
                pid = f"measure-{phase}-{payment:04d}"
                for sequence in (1, 2):
                    event = dict(
                        event_id=f"{pid}-{sequence}",
                        payment_id=pid,
                        merchant_id="M001",
                        event_type="initiated"
                        if sequence == 1
                        else ("declined" if payment % 4 == 0 else "authorised"),
                        sequence_no=sequence,
                        event_time=stamp(emitted - timedelta(milliseconds=2 - sequence)),
                        ingestion_time=stamp(emitted),
                        amount_minor=4200,
                        currency="GBP",
                    )
                    record = dict(
                        event_id=event["event_id"],
                        event_time=event["event_time"],
                        phase=phase,
                        submitted_at=stamp(datetime.now(UTC)),
                    )
                    records.append(record)
                    by_id[record["event_id"]] = record

                    def ack(error, message, record=record):
                        if error:
                            errors.append(str(error))
                        else:
                            record.update(
                                partition=message.partition(),
                                offset=message.offset(),
                                acknowledged_at=stamp(datetime.now(UTC)),
                            )

                    producer.produce(
                        topic, key=pid.encode(), value=encode(event, DATASET_ID, emitted), on_delivery=ack
                    )
                observe()
            # Include the last scheduled payment interval in the offered-rate denominator.
            while time.monotonic() < next_due:
                observe()
                time.sleep(0.01)
            submission_seconds = time.monotonic() - phase_start
            drain()
            phases.append(
                dict(
                    phase=phase,
                    events=payments * 2,
                    submission_seconds=submission_seconds,
                    offered_events_per_second=payments * 2 / submission_seconds,
                    scheduling_lag_seconds=distribution(lags),
                )
            )
            print(f"{phase}: {payments * 2} submitted, acknowledged and visible", flush=True)
        assert producer.flush(35) == 0 and not errors
        measured = [r for r in records if r["phase"] == "measured"]
        first = min(utc(r["submitted_at"]) for r in measured)
        last = max(utc(r["observed_at"]) for r in measured)
        last_ack = max(utc(r["acknowledged_at"]) for r in measured)
        report = dict(
            token=token,
            topic=topic,
            cluster_id=source.cluster_id,
            topic_id=source.topic_id,
            records=records,
            phases=phases,
            backlog=backlog,
            clock_checks=clock_checks,
            max_observation_gap_seconds=max(b - a for a, b in zip(poll_times, poll_times[1:], strict=False)),
            observed_events_per_second=len(measured) / (last - first).total_seconds(),
            final_ack_to_all_visible_seconds=(last - last_ack).total_seconds(),
            max_submitted_not_observed=max(b["not_observed"] for b in backlog),
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + "\n")
        print(
            json.dumps({k: v for k, v in report.items() if k not in ("records", "clock_checks", "backlog")})
        )


if __name__ == "__main__":
    main()
