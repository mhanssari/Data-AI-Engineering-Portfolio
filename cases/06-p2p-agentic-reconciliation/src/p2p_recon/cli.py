"""Command line for every pipeline step: `uv run p2p --help`."""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

import psycopg
import typer

from p2p_recon import conform as conform_step
from p2p_recon import documents as documents_step
from p2p_recon import dq as dq_step
from p2p_recon import evaluation as evaluation_step
from p2p_recon import generate as generate_step
from p2p_recon import graph as graph_step
from p2p_recon import ingest as ingest_step
from p2p_recon import mdm as mdm_step
from p2p_recon import migrate as migrate_step
from p2p_recon import ontology as ontology_step
from p2p_recon import recon as recon_step
from p2p_recon import retrieval as retrieval_step
from p2p_recon.agents import workflow
from p2p_recon.agents.providers import get_provider
from p2p_recon.config import GENERATED_DIR, ONTOLOGY_DIR, SAMPLES_DIR
from p2p_recon.db import connect

app = typer.Typer(
    no_args_is_help=True, add_completion=False, help="Case 06: procure-to-pay reconciliation pipeline."
)

DEFAULT_CUTOFF = generate_step.CUTOFF.isoformat()
Cutoff = Annotated[str, typer.Option(help="Month-end cut-off date for GRNI, YYYY-MM-DD.")]


def _show(result: object) -> None:
    typer.echo(json.dumps(result, indent=2, default=str))


@app.command()
def migrate() -> None:
    """Apply SQL migrations once each, then re-apply views and functions."""
    _show({"applied": migrate_step.migrate()})


@app.command()
def generate(seed: int = generate_step.DEFAULT_SEED, out: Path = GENERATED_DIR) -> None:
    """Write the synthetic batches, documents and expected outcomes."""
    _show(generate_step.generate(out, seed, SAMPLES_DIR))


@app.command()
def ingest(
    batch: Annotated[str, typer.Argument(help="Batch folder name, for example batch_001.")],
    root: Path = GENERATED_DIR,
) -> None:
    """Land one batch in raw. Re-delivering a batch changes nothing."""
    _show(ingest_step.ingest_batch(root / batch))


@app.command()
def conform() -> None:
    """Upsert changed raw rows into core; unresolved references go to quarantine."""
    _show(conform_step.conform())


@app.command("resolve-suppliers")
def resolve_suppliers() -> None:
    """Rebuild golden suppliers, the crosswalk and the steward queue."""
    _show(mdm_step.resolve_suppliers())


@app.command("steward-decide")
def steward_decide(
    record_a: Annotated[str, typer.Argument(help="SOURCE_SYSTEM:supplier_id")],
    record_b: Annotated[str, typer.Argument(help="SOURCE_SYSTEM:supplier_id")],
    decision: Annotated[str, typer.Argument(help="MERGE or REJECT")],
    decided_by: Annotated[str, typer.Option(help="Name of the steward.")],
) -> None:
    """Record a steward decision on a pair. It takes effect on the next resolve-suppliers."""
    if decision not in ("MERGE", "REJECT"):
        raise typer.BadParameter("decision must be MERGE or REJECT")
    mdm_step.record_steward_decision(
        tuple(record_a.split(":", 1)), tuple(record_b.split(":", 1)), decision, decided_by
    )
    typer.echo("Recorded.")


@app.command()
def match(cutoff: Cutoff = DEFAULT_CUTOFF) -> None:
    """Run controls, matching, duplicates and GRNI in SQL, storing a new snapshot in recon."""
    _show(recon_step.run_matching(date.fromisoformat(cutoff)))


@app.command()
def dq() -> None:
    """Run data-quality checks and reconciliations. Exits non-zero if any check fails."""
    checks = dq_step.run_dq()
    for check in checks:
        if check.status != "pass":
            typer.echo(
                f"{check.status.upper():5} {check.name} [{check.scope}] expected={check.expected} actual={check.actual} {check.detail}"
            )
    counts = {status: sum(1 for c in checks if c.status == status) for status in ("pass", "warn", "fail")}
    _show(counts)
    if counts["fail"]:
        raise typer.Exit(1)


@app.command("graph-sync")
def graph_sync() -> None:
    """Rebuild the Neo4j projection and reconcile its counts to PostgreSQL."""
    result = graph_step.sync_graph()
    _show(result)
    if result["reconciliation_failures"]:
        raise typer.Exit(1)


@app.command("graph-query")
def graph_query(
    name: Annotated[str, typer.Argument(help="File name in graph/queries, without the extension.")],
    param: Annotated[list[str] | None, typer.Option(help="key=value; repeat as needed.")] = None,
) -> None:
    """Run a query from the Cypher library."""
    parameters = dict(item.split("=", 1) for item in param or [])
    _show(graph_step.run_query(name, **parameters))


@app.command("validate-ontology")
def validate_ontology() -> None:
    """Validate the example instance data against the SHACL shapes."""
    from rdflib import Graph

    for example in ("valid-chain", "invalid-chain"):
        result = ontology_step.validate_graph(Graph().parse(ONTOLOGY_DIR / "examples" / f"{example}.ttl"))
        _show({"example": example, "conforms": result.conforms, "violations": result.messages})


@app.command("run-all")
def run_all(
    cutoff: Cutoff = DEFAULT_CUTOFF, seed: int = generate_step.DEFAULT_SEED, with_graph: bool = True
) -> None:
    """Generate, then take both batches through every step, in order."""
    migrate()
    generate(seed, GENERATED_DIR)
    for batch in ("batch_001", "batch_002"):
        typer.echo(f"--- {batch}")
        ingest(batch, GENERATED_DIR)
        conform_step.conform()
        resolve_suppliers()
        match(cutoff)
        dq()
    load_documents(GENERATED_DIR / "documents")
    if with_graph:
        graph_sync()


@app.command("load-documents")
def load_documents(documents_dir: Path = GENERATED_DIR / "documents") -> None:
    """Chunk and embed the generated documents into docs. The first run downloads the embedding model."""
    _show(documents_step.load_documents(documents_dir))


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="What to search for.")],
    acting_user: Annotated[str, typer.Option("--as", help="ap_gb01, ap_de01 or auditor.")] = "ap_gb01",
    method: Annotated[str, typer.Option(help="keyword, vector, hybrid or hybrid_graph.")] = "hybrid",
    invoice: Annotated[str | None, typer.Option(help="SOURCE_SYSTEM:invoice_id, for hybrid_graph.")] = None,
    k: int = 5,
) -> None:
    """Search the documents as a given user, and show what that user may cite."""
    anchor = tuple(invoice.split(":", 1)) if invoice else None
    with connect() as conn, graph_step.driver() as db:
        user = retrieval_step.load_user(conn, acting_user)
        hits = retrieval_step.search(conn, user, query, method, k, db, anchor)
    _show([{"citation": h.citation, "header": h.context_header, "content": h.content} for h in hits])


@app.command("eval-retrieval")
def eval_retrieval() -> None:
    """Measure recall@k and MRR per retrieval method and write the report to evidence."""
    _show(evaluation_step.evaluate_retrieval())


@app.command("agent-run")
def agent_run(
    acting_user: Annotated[str, typer.Option("--as", help="ap_gb01, ap_de01 or auditor.")] = "ap_gb01",
    provider: Annotated[
        str | None, typer.Option(help="offline, anthropic or ollama. Default: LLM_PROVIDER.")
    ] = None,
    limit: Annotated[int | None, typer.Option(help="Investigate at most this many held lines.")] = None,
) -> None:
    """Investigate the held lines this user may see, writing proposals to the review queue. Nothing else changes."""
    with workflow.agent_connection() as conn, graph_step.driver() as db:
        run = workflow.AgentRun(conn, db, get_provider(provider), acting_user, {"limit": limit})
        lines = workflow.open_lines(conn, run.user.company_codes, limit)
        outcomes = [run.investigate(line) for line in lines]
        run.finish()
    _show(
        {"agent_run_id": run.run_id, "lines": len(outcomes), "proposals": [o.proposal_id for o in outcomes]}
    )


@app.command("agent-eval")
def agent_eval(
    provider: Annotated[
        str | None, typer.Option(help="offline, anthropic or ollama. Default: LLM_PROVIDER.")
    ] = None,
    all_lines: Annotated[
        bool, typer.Option(help="Also run every background line, not only the planted ones.")
    ] = False,
) -> None:
    """Run the planted scenarios through the agents, measure them and write the report to evidence."""
    result = evaluation_step.evaluate_agents(provider, all_lines)
    _show(result["summary"])
    if result["summary"]["false_auto_approvals"] or result["hostile"]["false_auto_approvals"]:
        raise typer.Exit(1)


@app.command("review-list")
def review_list(
    acting_user: Annotated[str, typer.Option("--as", help="ap_gb01, ap_de01 or auditor.")] = "ap_gb01",
) -> None:
    """Show open proposals for the companies this user may see."""
    with connect() as conn:
        user = retrieval_step.load_user(conn, acting_user)
        _show(
            conn.execute(
                "select proposal_id, company_code, invoice_id, line_number, sql_status, sql_reasons, proposed_action, explanation "
                "from agent.review_queue where company_code = any(%s) order by proposal_id",
                (user.company_codes,),
            ).fetchall()
        )


@app.command("review-decide")
def review_decide(
    proposal_id: Annotated[int, typer.Argument()],
    decision: Annotated[str, typer.Argument(help="ACCEPT, OVERRIDE or QUERY.")],
    decided_by: Annotated[str, typer.Option("--as", help="ap_gb01, ap_de01 or auditor.")],
    note: str | None = None,
) -> None:
    """Record a person's decision on a proposal. It records only: nothing is posted or paid."""
    with connect() as conn:
        user = retrieval_step.load_user(conn, decided_by)
        company = conn.execute(
            "select company_code from agent.proposal where proposal_id = %s", (proposal_id,)
        ).fetchone()
        if company is None or company["company_code"] not in user.company_codes:
            raise typer.BadParameter(f"{decided_by} has no proposal {proposal_id} to decide")
        conn.execute(
            "insert into agent.human_decision (proposal_id, decision, decided_by, note) values (%s, %s, %s, %s)",
            (proposal_id, decision, decided_by, note),
        )
    typer.echo("Recorded.")


@app.command()
def status() -> None:
    """Summarise current results: statuses, exceptions, GRNI, quarantine and the latest data-quality run."""
    with connect() as conn:
        _show(
            {
                "latest_recon_run": conn.execute("select * from recon.latest_run").fetchone(),
                "lines_by_status": conn.execute(
                    "select status_code, count(*) as lines from recon.current_match_result group by 1 order by 1"
                ).fetchall(),
                "exceptions_by_reason": conn.execute(
                    "select reason_code, count(*) as lines from recon.current_exception group by 1 order by 2 desc, 1"
                ).fetchall(),
                "value_held_by_currency": conn.execute(
                    "select company_code, currency_code, sum(net_amount) as value_held from recon.current_match_result "
                    "where status_code in ('Exception', 'PendingReceipt') group by 1, 2 order by 1, 2"
                ).fetchall(),
                "grni_by_currency": conn.execute(
                    "select company_code, currency_code, cutoff_date, count(*) as lines, sum(grni_amount) as grni "
                    "from recon.current_grni_line group by 1, 2, 3 order by 1, 2"
                ).fetchall(),
                "golden_suppliers": conn.execute(
                    "select count(*) as golden, sum(member_count) as source_records from mdm.supplier_golden"
                ).fetchone(),
                "steward_queue": conn.execute(
                    "select count(*) as pairs from mdm.supplier_match_candidate where decision = 'STEWARD_REVIEW'"
                ).fetchone(),
                "open_quarantine": conn.execute(
                    "select count(*) as rows from ops.quarantine where resolved_run_id is null"
                ).fetchone(),
                "latest_dq": conn.execute(
                    "select status, count(*) as checks from ops.dq_result where run_id = "
                    "(select max(run_id) from ops.pipeline_run where step = 'dq') group by 1 order by 1"
                ).fetchall(),
            }
        )


@app.command()
def reset(
    yes: Annotated[
        bool, typer.Option("--yes", help="Confirm dropping every pipeline schema in the local database.")
    ] = False,
) -> None:
    """Drop all pipeline schemas in the configured local database. Regenerable: everything comes from the generator."""
    if not yes:
        raise typer.BadParameter("Pass --yes to confirm.")
    with connect() as conn:
        conn.execute("drop schema if exists agent, docs, recon, mdm, core, stg, raw, ops cascade")
    typer.echo(f"Schemas dropped at {datetime.now().isoformat(timespec='seconds')}.")


# Referenced so that a connection failure reads as a message, not a traceback.
def main() -> None:
    try:
        app()
    except psycopg.OperationalError as exc:
        typer.echo(f"Cannot reach PostgreSQL. Is the container running? ({exc})", err=True)
        raise SystemExit(2) from exc
