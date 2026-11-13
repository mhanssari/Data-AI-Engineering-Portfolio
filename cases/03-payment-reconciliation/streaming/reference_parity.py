"""Independent disposable batch reference for the fixed recovery fixture. Used only by integration checks."""

import csv
import hashlib
import json
import os
import re
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from psycopg import sql

from payrecon import core, db, generator, loader
from payrecon.config import FILE_COLUMNS, SEED
from payrecon.streaming.contract import ContractError, Dataset, decode, utc
from payrecon.streaming.live_fixture import phase_records


def compare(con, generation):
    database = os.environ.get("PGDATABASE", "")
    if not re.fullmatch(r"payments_stream_check_[a-f0-9]{12}", database):
        raise ValueError("batch reference is restricted to an isolated integration database")
    reference_db = "payrecon_test_repair_" + database.rsplit("_", 1)[1]
    dataset = Dataset("case03-intake-v1", utc("2026-10-01T00:00:00Z"), utc("2026-11-01T00:00:00Z"))
    events = {}
    for phase in ("initial", "advance", "late-gap"):
        for key, value in phase_records(phase):
            try:
                event, _ = decode(key, value, dataset)
            except ContractError:
                continue
            events[event["event_id"]] = event
    world = generator.build_world(SEED)
    merchants = [
        m
        for m in [*world.merchants, *world.late_merchants]
        if m in world.merchants or m.updated_at.date() + timedelta(days=1) <= date(2026, 10, 12)
    ]
    sources = {
        "merchants.csv": (
            "merchants",
            [
                dict(
                    merchant_id=m.merchant_id,
                    merchant_name=m.merchant_id,
                    country=generator.COUNTRIES[m.currency],
                    currency=m.currency,
                    fee_bps=m.fee_bps,
                    fee_fixed_minor=m.fee_fixed_minor,
                    updated_at=m.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
                )
                for m in merchants
            ],
        ),
        "events_2026-10-13.csv": (
            "events",
            [
                dict(
                    e,
                    event_time=e["event_time"].replace("T", " ").removesuffix("Z"),
                    ingestion_time=e["ingestion_time"].replace("T", " ").removesuffix("Z"),
                )
                for e in events.values()
            ],
        ),
    }
    with db.connect("postgres") as admin:
        if admin.execute("select 1 from pg_database where datname=%s", [reference_db]).fetchone():
            raise ValueError("unique batch reference database already exists; refusing to replace it")
        admin.execute(sql.SQL("create database {}").format(sql.Identifier(reference_db)))
        try:
            with db.connect(reference_db) as reference, TemporaryDirectory(prefix="payrecon-parity-") as temp:
                folder = Path(temp)
                manifest = []
                for name, (kind, rows) in sources.items():
                    file = folder / name
                    with file.open("w", encoding="utf-8", newline="") as handle:
                        writer = csv.DictWriter(handle, fieldnames=FILE_COLUMNS[kind], lineterminator="\n")
                        writer.writeheader()
                        writer.writerows(rows)
                    manifest.append(
                        dict(
                            file=name,
                            kind=kind,
                            rows=len(rows),
                            sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
                        )
                    )
                (folder / "manifest.json").write_text(
                    json.dumps(dict(arrival_date="2026-10-14", files=manifest)), encoding="utf-8"
                )
                db.migrate(reference)
                loader.load_arrival(reference, folder)
                core.build(reference, date(2026, 10, 14))
                columns = "payment_id,merchant_id,currency,status,last_sequence_no,held_events,initiated_at,captured_at,status_changed_at,captured_minor,refunded_minor,reversed_minor,expected_settlement_minor"
                expected = reference.execute(
                    f"select {columns} from core.payment order by payment_id"
                ).fetchall()
                actual = con.execute(
                    f"select {columns} from stream.payment_latest where generation=%s order by payment_id",
                    [generation],
                ).fetchall()
                assert actual == expected, "repaired live payment business columns differ from batch"
                expected_minutes = reference.execute(
                    "select date_trunc('minute',e.event_time) at time zone 'UTC',e.merchant_id,e.currency,e.event_type,count(*) "
                    "from core.payment_event e join core.payment p using(payment_id) "
                    "left join core.illegal_transition i using(event_id) "
                    "where i.event_id is null and e.sequence_no<=p.last_sequence_no "
                    "group by 1,2,3,4 order by 1,2,3,4"
                ).fetchall()
                actual_minutes = con.execute(
                    "select minute_start,merchant_id,currency,event_type,event_count from stream.event_minute "
                    "where generation=%s and outcome='applied' order by 1,2,3,4",
                    [generation],
                ).fetchall()
                assert actual_minutes == expected_minutes, (
                    "repaired live minute counts differ from batch eligibility"
                )
                illegal = reference.execute(
                    "select event_id from core.illegal_transition order by event_id"
                ).fetchall()
                assert (
                    illegal
                    == con.execute(
                        "select event_id from stream.event_application where generation=%s and outcome='illegal' order by event_id",
                        [generation],
                    ).fetchall()
                )
                return dict(
                    payments=len(expected),
                    applied_events=sum(r[-1] for r in expected_minutes),
                    minute_groups=len(expected_minutes),
                    illegal_events=len(illegal),
                )
        finally:
            admin.execute(sql.SQL("drop database {} with (force)").format(sql.Identifier(reference_db)))
