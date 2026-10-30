"""Move raw rows into the canonical core model, incrementally and safely to repeat.

For each entity, in dependency order:
  1. Find keys with a row changed after (high watermark - lookback), plus keys with an open quarantine entry.
  2. Take the latest raw version of those keys. Latest is by source change time, so an out-of-order delivery loses.
  3. Check references. A row whose parent is missing goes to ops.quarantine with a reason and is retried next run.
  4. Upsert if newer. Repeating a run writes nothing; a CDC delete becomes a soft delete.

The lookback is what catches a late arrival stamped before the watermark. A row older than the lookback is missed by
design, and the row-count reconciliation in dq.py reports it rather than letting it pass silently.
"""

from dataclasses import dataclass, field

import psycopg

from p2p_recon.db import Run, connect, pipeline_run

LINEAGE_COLUMNS = {"source_raw_id", "source_changed_at", "last_run_id", "is_deleted"}


@dataclass(frozen=True)
class Ref:
    parent: str
    columns: dict[str, str]  # child column -> parent column
    required: bool = True


@dataclass(frozen=True)
class Entity:
    name: str
    key: tuple[str, ...]
    refs: tuple[Ref, ...] = field(default_factory=tuple)

    @property
    def table(self) -> str:
        return f"core.{self.name}"


def _company() -> Ref:
    return Ref("core.legal_entity", {"company_code": "company_code"})


def _item() -> Ref:
    return Ref("core.item", {"item_id": "item_id"})


def _supplier() -> Ref:
    return Ref(
        "core.supplier", {"source_system": "source_system", "source_supplier_id": "source_supplier_id"}
    )


def _budget() -> tuple[Ref, Ref]:
    return (
        Ref("core.cost_centre", {"company_code": "company_code", "cost_centre_code": "cost_centre_code"}),
        Ref("core.gl_account", {"company_code": "company_code", "gl_account_code": "gl_account_code"}),
    )


# Parents before children, so that a chain whose parent has just arrived clears in a single run.
ENTITIES = (
    Entity("legal_entity", ("company_code",)),
    Entity("policy_parameter", ("parameter_code", "policy_version")),
    Entity("fx_rate", ("from_currency", "to_currency", "valid_from")),
    Entity("item", ("item_id",)),
    Entity("cost_centre", ("company_code", "cost_centre_code"), (_company(),)),
    Entity("gl_account", ("company_code", "gl_account_code"), (_company(),)),
    Entity("supplier", ("source_system", "source_supplier_id"), (_company(),)),
    Entity("supplier_bank_account", ("source_system", "source_supplier_id", "valid_from"), (_supplier(),)),
    Entity("employee", ("source_system", "employee_id", "valid_from"), (_company(),)),
    Entity("contract", ("contract_id", "valid_from"), (_company(), _item(), _supplier())),
    Entity(
        "purchase_requisition",
        ("source_system", "pr_number"),
        (
            _company(),
            _item(),
            *_budget(),
            Ref("core.employee", {"source_system": "source_system", "requester_id": "employee_id"}),
            Ref(
                "core.supplier",
                {"source_system": "source_system", "source_supplier_id": "source_supplier_id"},
                required=False,
            ),
        ),
    ),
    Entity(
        "approval",
        ("source_system", "pr_number", "step"),
        (
            Ref("core.purchase_requisition", {"source_system": "source_system", "pr_number": "pr_number"}),
            Ref("core.employee", {"source_system": "source_system", "approver_id": "employee_id"}),
        ),
    ),
    Entity("purchase_order", ("source_system", "po_number"), (_company(), _supplier())),
    Entity(
        "po_line",
        ("source_system", "po_number", "line_number"),
        (
            Ref("core.purchase_order", {"source_system": "source_system", "po_number": "po_number"}),
            _item(),
            *_budget(),
            Ref(
                "core.purchase_requisition",
                {"source_system": "source_system", "pr_number": "pr_number"},
                required=False,
            ),
            Ref("core.contract", {"contract_id": "contract_id"}, required=False),
        ),
    ),
    Entity("goods_receipt", ("source_system", "grn_number"), (_company(),)),
    Entity(
        "grn_line",
        ("source_system", "grn_number", "line_number"),
        (
            Ref("core.goods_receipt", {"source_system": "source_system", "grn_number": "grn_number"}),
            Ref(
                "core.po_line",
                {"source_system": "source_system", "po_number": "po_number", "po_line_number": "line_number"},
            ),
        ),
    ),
    Entity("supplier_invoice", ("source_system", "invoice_id"), (_company(), _supplier())),
    Entity(
        "invoice_line",
        ("source_system", "invoice_id", "line_number"),
        (
            Ref("core.supplier_invoice", {"source_system": "source_system", "invoice_id": "invoice_id"}),
            Ref(
                "core.po_line",
                {"source_system": "source_system", "po_number": "po_number", "po_line_number": "line_number"},
                required=False,
            ),
        ),
    ),
)


def _reason_sql(entity: Entity) -> str:
    cases = []
    for ref in entity.refs:
        join = " and ".join(f"p.{parent} = l.{child}" for child, parent in ref.columns.items())
        missing = f"not exists (select 1 from {ref.parent} p where {join})"
        if not ref.required:
            present = " and ".join(
                f"l.{child} is not null" for child in ref.columns if child != "source_system"
            )
            missing = f"({present}) and {missing}"
        cases.append(f"case when {missing} then 'missing parent in {ref.parent}' end")
    if not cases:
        return "null::text"
    return "nullif(concat_ws('; ', " + ", ".join(cases) + "), '')"


def _conform_entity(conn: psycopg.Connection, run: Run, entity: Entity) -> dict[str, int]:
    name = entity.name
    keys = ", ".join(entity.key)
    natural = "concat_ws('|', " + ", ".join(f"s.{k}::text" for k in entity.key) + ")"
    columns = [
        r["column_name"]
        for r in conn.execute(
            "select column_name from information_schema.columns where table_schema = 'core' and table_name = %s "
            "order by ordinal_position",
            (name,),
        ).fetchall()
        if r["column_name"] not in LINEAGE_COLUMNS
    ]
    column_list = ", ".join(columns)
    updates = ", ".join(f"{c} = excluded.{c}" for c in columns if c not in entity.key)

    # Entity names and keys are constants above, never input, so composing identifiers here is safe.
    conn.execute(f"""
        create temp table _src on commit drop as
        with in_window as (
            select {natural} as natural_key
            from stg.{name} s
            left join ops.watermark w on w.source_system = s.source_system and w.entity = '{name}'
            where s.source_changed_at > coalesce(w.high_watermark, '-infinity'::timestamptz)
                                        - coalesce(w.lookback, interval '24 hours')
            union
            select natural_key from ops.quarantine where entity = '{name}' and resolved_run_id is null
        ),
        latest as (
            select distinct on ({keys}) s.*, {natural} as natural_key
            from stg.{name} s
            order by {keys}, s.source_changed_at desc, s.raw_id desc
        )
        select l.*, {_reason_sql(entity)} as reason
        from latest l join in_window using (natural_key)
    """)

    upserted = conn.execute(f"""
        insert into {entity.table} ({column_list}, source_raw_id, source_changed_at, is_deleted, last_run_id)
        select {column_list}, raw_id, source_changed_at, coalesce(cdc_op, '') = 'D', {run.run_id}
        from _src where reason is null
        on conflict ({keys}) do update set {updates},
            source_raw_id = excluded.source_raw_id, source_changed_at = excluded.source_changed_at,
            is_deleted = excluded.is_deleted, last_run_id = excluded.last_run_id
        where ({entity.table}.source_changed_at, {entity.table}.source_raw_id)
            < (excluded.source_changed_at, excluded.source_raw_id)
        returning (xmax = 0) as inserted
    """).fetchall()

    quarantined = conn.execute(f"""
        insert into ops.quarantine (entity, source_system, natural_key, raw_id, reason, first_run_id, last_run_id)
        select '{name}', source_system, natural_key, raw_id, reason, {run.run_id}, {run.run_id}
        from _src where reason is not null
        on conflict (entity, raw_id) do update set reason = excluded.reason, last_run_id = excluded.last_run_id
    """).rowcount
    released = conn.execute(f"""
        update ops.quarantine q set resolved_run_id = {run.run_id}
        from _src s
        where q.entity = '{name}' and q.resolved_run_id is null and q.natural_key = s.natural_key and s.reason is null
    """).rowcount

    conn.execute(f"""
        insert into ops.watermark (source_system, entity, high_watermark, updated_run_id)
        select source_system, '{name}', max(source_changed_at), {run.run_id}
        from stg.{name} group by source_system
        on conflict (source_system, entity) do update
        set high_watermark = greatest(ops.watermark.high_watermark, excluded.high_watermark),
            updated_run_id = excluded.updated_run_id
    """)
    conn.execute("drop table _src")

    inserted = sum(1 for r in upserted if r["inserted"])
    run.lineage(conn, f"stg.{name}", entity.table, len(upserted))
    return {
        "inserted": inserted,
        "updated": len(upserted) - inserted,
        "quarantined": quarantined,
        "released": released,
    }


def conform() -> dict:
    with pipeline_run("conform") as run, connect() as conn:
        run.counts = {entity.name: _conform_entity(conn, run, entity) for entity in ENTITIES}
        return run.counts
