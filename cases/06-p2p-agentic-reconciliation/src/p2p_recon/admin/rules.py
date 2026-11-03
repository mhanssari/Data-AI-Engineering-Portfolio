"""How the agents decide, read from what actually decides: the matching SQL and its registry, the policy parameters, the
compiled agent graph, the guard, the offline provider's rule table and the decision policy in PostgreSQL.

Descriptions live beside what they describe (sql/rules.yaml, guard.FLAGS), and tests compare each against its source, so
the page cannot drift into describing a system that no longer exists.
"""

import re
from functools import lru_cache

import psycopg
import yaml
from rdflib import SKOS, Graph, Literal

from p2p_recon.admin import agents
from p2p_recon.agents import workflow
from p2p_recon.agents.guard import FALLBACK_ACTION, FLAGS
from p2p_recon.agents.providers import OfflineProvider
from p2p_recon.config import EVALUATION_DIR, ONTOLOGY_DIR, REFERENCE_DIR, SQL_DIR
from p2p_recon.ontology import VOC

RECON_SQL = SQL_DIR / "routines" / "30_recon.sql"
REGISTRY = SQL_DIR / "rules.yaml"
# In 30_recon.sql a reason code is always a literal followed directly by its evidence.
_REASON_LITERAL = re.compile(r"'([A-Z][A-Za-z]+)',\s*(?:'\{\}'::jsonb|jsonb_build_object)")
_POLICY_CALL = re.compile(r"policy_value\('(\w+)'")
_TIER_PATTERN = "parameter_code like 'approval_tier_%'"


# ---------------------------------------------------------------- static sources (files in the repository, not role data)


@lru_cache
def registry() -> dict:
    return yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))


@lru_cache
def _vocabulary() -> Graph:
    return Graph().parse(ONTOLOGY_DIR / "vocab.ttl")


def sql_reason_codes() -> set[str]:
    return set(_REASON_LITERAL.findall(RECON_SQL.read_text(encoding="utf-8")))


def sql_policy_codes() -> set[str]:
    """Every policy parameter the matching SQL reads, including the approval tiers it selects by pattern."""
    text = RECON_SQL.read_text(encoding="utf-8")
    codes = set(_POLICY_CALL.findall(text))
    if _TIER_PATTERN in text:
        policy = yaml.safe_load((REFERENCE_DIR / "p2p_policy.yaml").read_text(encoding="utf-8"))
        codes |= {code for code in policy["parameters"] if code.startswith("approval_tier_")}
    return codes


def _english(values: list) -> str | None:
    english = [str(v) for v in values if isinstance(v, Literal) and v.language in (None, "en")]
    return english[0] if english else None


def concept(code: str) -> dict:
    """A SKOS concept's English label, definition and broader concept. Missing values are None, not invented."""
    graph, node = _vocabulary(), VOC[code]
    broader = list(graph.objects(node, SKOS.broader))
    return {
        "in_vocabulary": (node, SKOS.inScheme, None) in graph,
        "label": _english(list(graph.objects(node, SKOS.prefLabel))),
        "definition": _english(list(graph.objects(node, SKOS.definition))),
        "broader": _english(list(graph.objects(broader[0], SKOS.prefLabel))) if broader else None,
    }


# ---------------------------------------------------------------- business rules


def catalogue(conn: psycopg.Connection) -> list[dict]:
    """One row per rule, with how often it fired in the latest matching run."""
    lines = {
        r["reason_code"]: r["n"]
        for r in conn.execute("select reason_code, count(*) as n from recon.current_exception group by 1")
    }
    requisitions = {
        r["reason_code"]: r["n"]
        for r in conn.execute(
            "select reason_code, count(*) as n from recon.current_control_breach group by 1"
        )
    }
    rows = []
    for code, rule in registry()["rules"].items():
        skos = concept(code)
        rows.append(
            {
                "reason_code": code,
                "kind": skos["broader"],
                "label": skos["label"],
                "definition": skos["definition"],
                "level": rule["level"],
                "description": rule["description"],
                "parameters": rule["parameters"],
                "contract_tolerance": rule.get("contract_tolerance", False),
                "scenarios": rule["scenarios"],
                "lines_held": lines.get(code, 0),
                "requisitions_breached": requisitions.get(code, 0)
                if rule["level"] == "requisition"
                else None,
            }
        )
    return rows


def parameters(conn: psycopg.Connection, codes: list[str]) -> list[dict]:
    """Every version of each parameter, newest last. The SQL reads the version in force on the document date."""
    return conn.execute(
        """
        select parameter_code, policy_version, parameter_value, unit, valid_from
        from core.policy_parameter where parameter_code = any(%s) and not is_deleted
        order by parameter_code, valid_from
        """,
        (codes,),
    ).fetchall()


def contract_tolerances(conn: psycopg.Connection) -> list[dict]:
    return conn.execute(
        """
        select contract_id, company_code, valid_from, valid_to, price_tolerance_pct
        from core.contract where not is_deleted order by contract_id, valid_from
        """
    ).fetchall()


def statuses(conn: psycopg.Connection) -> list[dict]:
    counts = {
        r["status_code"]: r["n"]
        for r in conn.execute("select status_code, count(*) as n from recon.current_match_result group by 1")
    }
    return [
        {
            "status": status,
            "definition": concept(status)["definition"],
            "how_it_is_decided": spec["description"],
            "parameters": spec["parameters"],
            "lines_now": counts.get(status, 0),
        }
        for status, spec in registry()["statuses"].items()
    ]


# ---------------------------------------------------------------- agent workflow

START, END = "__start__", "__end__"
# Agent steps are logged under these names; the guard runs inside the submit node.
STEP_NODE = {
    "supervisor": "supervisor",
    "controls": "controls",
    "matching": "matching",
    "investigator": "investigator",
    "guard": "submit",
}
# What each branch of a conditional edge means. A test fails if a key here is not an edge of the compiled graph.
BRANCHES = {
    ("supervisor", END): "denied: the acting user cannot see the line",
    ("supervisor", "controls"): "visible",
    ("matching", "auto_draft"): "clean in SQL",
    ("matching", "investigator"): "held in SQL",
}


def workflow_edges() -> list[tuple[str, str, bool]]:
    """(source, target, conditional) for every edge of the compiled LangGraph graph."""
    return [(e.source, e.target, e.conditional) for e in workflow.workflow_graph().edges]


def _flags_raised_in(place: str) -> list[str]:
    return [flag for flag, (where, _) in FLAGS.items() if where == place]


def _notes() -> dict[str, str]:
    return {
        # One flag per line: the browser's Graphviz sizes boxes slightly differently from how it draws text, and long
        # lines run out of the box.
        "investigator": (
            f"Model loop: at most {workflow.MAX_MODEL_STEPS} calls, {workflow.MAX_TOKENS_PER_LINE:,} tokens.\n"
            "Outage and limits go to a person:\n" + "\n".join(_flags_raised_in("investigator"))
        ),
        "submit": (
            "Guard (apply_guard), then the proposal is stored.\nChecks:\n"
            + "\n".join(_flags_raised_in("guard"))
            + "\nReview items appear in agent.review_queue."
        ),
    }


def _quote(text: object) -> str:
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def workflow_dot(edge_counts: dict[tuple[str, str], int] | None = None) -> str:
    """Graphviz text drawn from the compiled graph. Notes (shape=note) annotate what happens inside two nodes."""
    lines = [
        "digraph workflow {",
        "  rankdir=TB;",
        '  node [shape=box, style="rounded", fontsize=11];',
        "  edge [fontsize=10];",
        f'  {_quote(START)} [label="start", shape=circle];',
        f'  {_quote(END)} [label="end", shape=doublecircle];',
    ]
    for source, target, conditional in workflow_edges():
        label = BRANCHES.get((source, target), "")
        attributes = [f"label={_quote(label)}"] if label else []
        if conditional:
            attributes.append("style=dashed")
        if edge_counts is not None:
            n = edge_counts.get((source, target), 0)
            attributes.append(f"xlabel={_quote(f'{n} line(s)')}")
            attributes.append("penwidth=2.5" if n else 'color="#bbbbbb"')
        lines.append(f"  {_quote(source)} -> {_quote(target)} [{', '.join(attributes)}];")
    for node, text in _notes().items():
        note = f"note_{node}"
        lines.append(f'  {_quote(note)} [shape=note, label={_quote(text)}, fontsize=9, style=""];')
        lines.append(f"  {_quote(node)} -> {_quote(note)} [style=dotted, arrowhead=none];")
    lines.append("}")
    return "\n".join(lines)


def run_paths(conn: psycopg.Connection, agent_run_id: int) -> dict:
    """The path each line of one run took through the graph, rebuilt from its logged steps, and how many took each edge."""
    rows = conn.execute(
        """
        select source_system, invoice_id, line_number, agent_name, outcome->>'route' as route
        from agent.step where agent_run_id = %s order by step_id
        """,
        (agent_run_id,),
    ).fetchall()
    steps: dict[tuple, list[dict]] = {}
    for row in rows:
        steps.setdefault((row["source_system"], row["invoice_id"], row["line_number"]), []).append(row)
    lines, counts = [], {}
    for (source_system, invoice_id, line_number), logged in steps.items():
        names = {STEP_NODE.get(s["agent_name"], s["agent_name"]) for s in logged}
        path = [START, "supervisor"]
        if any(s["route"] == "denied" for s in logged):
            path.append(END)
        else:
            path += [n for n in ("controls", "matching") if n in names]
            if "investigator" in names:
                path.append("investigator")
            elif "submit" in names:
                path.append("auto_draft")
            if "submit" in names:
                path += ["submit", END]
        for edge in zip(path, path[1:], strict=False):
            counts[edge] = counts.get(edge, 0) + 1
        lines.append(
            {
                "source_system": source_system,
                "invoice_id": invoice_id,
                "line_number": line_number,
                "path": " → ".join(path[1:-1] if path[-1] == END else path[1:]),
                "finished": path[-1] == END,
            }
        )
    return {"lines": lines, "edges": counts}


# ---------------------------------------------------------------- guard and action mapping


def guard_checks(conn: psycopg.Connection, run_filter: agents.RunFilter) -> dict:
    """Each flag with what it does and how often it fired. Stored flags the catalogue does not know are listed apart."""
    run_ids = agents._run_ids(conn, run_filter)
    fired = {r["reason"]: r["n"] for r in agents.guard_interventions(conn, run_ids)["reasons"]}
    return {
        "checks": [
            {"flag": flag, "raised_in": where, "what_happens": text, "fired": fired.get(flag, 0)}
            for flag, (where, text) in FLAGS.items()
        ],
        "unknown": sorted(set(fired) - set(FLAGS)),
    }


@lru_cache
def _expected_actions() -> dict:
    return yaml.safe_load((EVALUATION_DIR / "expected_actions.yaml").read_text(encoding="utf-8"))


def action_mapping() -> list[dict]:
    """The offline provider's rule table, in the order it applies, beside the evaluation's hand-written expectations.

    Only the offline provider maps reasons to actions this way. A live model chooses its own action, and the guard limits
    what it may choose.
    """
    expected = _expected_actions()
    rows = []
    for priority, (code, (action, query)) in enumerate(OfflineProvider.RULES.items(), 1):
        wanted = expected["by_reason"].get(code, {}).get("action")
        rows.append(
            {
                "priority": priority,
                "when": f"reason {code}",
                "offline_action": action,
                "searches_for": query,
                "expected_action": wanted,
                "agrees": action == wanted,
            }
        )
    for status, (action, query) in OfflineProvider.STATUS_RULES.items():
        wanted = expected["by_status"].get(status)
        rows.append(
            {
                "priority": len(rows) + 1,
                "when": f"status {status}, no reason",
                "offline_action": action,
                "searches_for": query,
                "expected_action": wanted,
                "agrees": action == wanted,
            }
        )
    rows.append(
        {
            "priority": len(rows) + 1,
            "when": "anything else",
            "offline_action": FALLBACK_ACTION,
            "searches_for": None,
            "expected_action": None,
            "agrees": None,
        }
    )
    return rows


# ---------------------------------------------------------------- decisions and the proposal lifecycle

DECISIONS = {"ACCEPT": "Accepted", "OVERRIDE": "Overridden", "QUERY": "Queried"}
UNDECIDED = ("Awaiting review", "Auto-approved", "Superseded")
# Transitions made when a proposal is stored, not by a decision.
STORE_TRANSITIONS = (
    ("Stored", "Awaiting review", "action is not AUTO_APPROVE (requires_review)"),
    ("Stored", "Auto-approved", "the guard allowed AUTO_APPROVE: never queued"),
    ("Awaiting review", "Superseded", "a later live run stores a proposal for the same line"),
)


def lifecycle_transitions() -> list[dict]:
    """Every transition the lifecycle drawing shows, spelt out. The database accepts a decision on any stored proposal,
    including one already decided, and the latest decision counts; the drawing shows exactly that."""
    rows = [{"from": a, "to": b, "decision": None, "when": when} for a, b, when in STORE_TRANSITIONS]
    for state in (*UNDECIDED, *DECISIONS.values()):
        for decision, target in DECISIONS.items():
            rows.append({"from": state, "to": target, "decision": decision, "when": f"{decision} recorded"})
    return rows


def lifecycle_dot() -> str:
    """Decisions meet at one junction, so that every state that can be decided from is drawn once, not twelve times."""
    lines = [
        "digraph lifecycle {",
        "  rankdir=LR;",
        '  node [shape=box, style="rounded", fontsize=11];',
        "  edge [fontsize=10];",
        '  "decision" [shape=diamond, label="decision\\nrecorded", fontsize=10];',
    ]
    for source, target, when in STORE_TRANSITIONS:
        lines.append(f"  {_quote(source)} -> {_quote(target)} [label={_quote(when)}];")
    for state in UNDECIDED:
        lines.append(f'  {_quote(state)} -> "decision";')
    for decision, state in DECISIONS.items():
        lines.append(f'  "decision" -> {_quote(state)} [label={_quote(decision)}];')
        lines.append(f'  {_quote(state)} -> "decision" [style=dashed, label="later decision"];')
    lines.append("}")
    return "\n".join(lines)


def decision_policy(conn: psycopg.Connection) -> dict:
    """The policies on agent.human_decision as PostgreSQL holds them, and what the insert policy says, parsed from it."""
    policies = conn.execute(
        """
        select policyname, cmd, roles::text[] as roles, qual, with_check
        from pg_policies where schemaname = 'agent' and tablename = 'human_decision' order by policyname
        """
    ).fetchall()
    check = next((p["with_check"] for p in policies if p["cmd"] == "INSERT"), "") or ""
    controls = re.search(r"ARRAY\[(.*?)\]", check, re.DOTALL)
    return {
        "policies": policies,
        "privileged_roles": sorted(set(re.findall(r"CURRENT_USER = '(\w+)'::name", check, re.IGNORECASE))),
        "control_reasons": sorted(re.findall(r"'(\w+)'::text", controls.group(1))) if controls else [],
    }


def decision_rights(conn: psycopg.Connection) -> list[dict]:
    """Who may record a decision on which proposals, from grants, agent.acting_user and the insert policy.

    A role may decide when it holds INSERT on agent.human_decision, is listed in agent.acting_user (the foreign key on
    decided_by), can see the proposal's company, and, for a failed control, is named in the policy.
    """
    policy = decision_policy(conn)
    roles = conn.execute(
        """
        select u.user_name as role, u.company_codes, true as acting_user,
               has_table_privilege(u.user_name, 'agent.human_decision', 'INSERT') as can_insert
        from agent.acting_user u
        union all
        select r.role_name, r.company_codes, false, has_table_privilege(r.role_name, 'agent.human_decision', 'INSERT')
        from agent.reviewer r
        union all
        select 'agent_service', '{}'::text[], false, has_table_privilege('agent_service', 'agent.human_decision', 'INSERT')
        order by 1
        """
    ).fetchall()
    rows = []
    for role in roles:
        may = role["can_insert"] and role["acting_user"]
        rows.append(
            {
                "role": role["role"],
                "sees_companies": role["company_codes"],
                "ordinary_proposals": role["company_codes"] if may else [],
                "failed_control_proposals": role["company_codes"]
                if may and role["role"] in policy["privileged_roles"]
                else [],
            }
        )
    return rows


def may_decide(rights: list[dict], role: str, company_code: str, failed_control: bool) -> bool:
    row = next((r for r in rights if r["role"] == role), None)
    if row is None:
        return False
    return company_code in row["failed_control_proposals" if failed_control else "ordinary_proposals"]
