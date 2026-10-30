"""Supplier entity resolution: standardise, compare every pair, cluster, write golden records and a crosswalk.

Decision order for a pair, strongest evidence first:
  - same VAT number: auto-merge. VAT is the only identifier a supplier cannot share with an unrelated company.
  - both have a VAT number and they differ: never auto-merge, however similar the rest. Goes to a steward if similar.
  - same bank account: auto-merge only with a similar name. With an unrelated name it is a fraud signal, so a steward
    sees it and the records stay apart.
  - otherwise the fuzzy score, compared to the two thresholds.

A steward's decision overrides all of this and survives every rebuild. Comparing all pairs is fine for 60 suppliers;
at scale this needs blocking (for example on postcode or a name token) before scoring.
"""

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from itertools import combinations

import psycopg
from rapidfuzz import fuzz

from p2p_recon.db import connect, pipeline_run

# Scores are out of 100. Calibrated on the planted pairs and the background population: see docs/decisions/0004.
AUTO_MERGE_SCORE = Decimal("92")
REVIEW_SCORE = Decimal("75")
BANK_NAME_FLOOR = Decimal("60")
NAME_WEIGHT, ADDRESS_WEIGHT = Decimal("0.7"), Decimal("0.3")

LEGAL_SUFFIXES = {"LTD", "LIMITED", "PLC", "LLP", "GMBH", "AG", "KG", "UG", "CO", "EK", "SE"}
ADDRESS_WORDS = {"STRASSE": "STR", "ROAD": "RD", "STREET": "ST", "LANE": "LN", "SQUARE": "SQ"}
TRANSLITERATE = str.maketrans({"Ä": "AE", "Ö": "OE", "Ü": "UE"})


def standardise_text(value: str | None) -> str:
    text = (value or "").upper().translate(TRANSLITERATE)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", text)).strip()


def standardise_name(value: str | None) -> str:
    return " ".join(word for word in standardise_text(value).split() if word not in LEGAL_SUFFIXES)


def standardise_address(line: str | None, city: str | None, postcode: str | None) -> str:
    words = standardise_text(f"{line or ''} {postcode or ''} {city or ''}").split()
    return " ".join(ADDRESS_WORDS.get(word, word) for word in words)


def standardise_vat(value: str | None) -> str | None:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper()) or None


@dataclass
class Record:
    source_system: str
    source_supplier_id: str
    name: str
    raw_name: str
    address: str
    vat: str | None
    country: str | None
    ibans: set[str]
    changed_at: object

    @property
    def key(self) -> tuple[str, str]:
        return (self.source_system, self.source_supplier_id)


@dataclass
class PairDecision:
    a: Record
    b: Record
    method: str
    name_score: Decimal
    address_score: Decimal
    combined: Decimal
    decision: str
    evidence: str


def score(a: Record, b: Record) -> tuple[Decimal, Decimal, Decimal]:
    # token_set handles an extra word in one name ("Food"); token_sort alone would punish it heavily.
    name = Decimal(
        str(round((fuzz.token_sort_ratio(a.name, b.name) + fuzz.token_set_ratio(a.name, b.name)) / 2, 2))
    )
    address = Decimal(str(round(fuzz.token_sort_ratio(a.address, b.address), 2)))
    return name, address, (NAME_WEIGHT * name + ADDRESS_WEIGHT * address).quantize(Decimal("0.01"))


def decide(a: Record, b: Record) -> PairDecision | None:
    name, address, combined = score(a, b)

    def result(method: str, decision: str, evidence: str) -> PairDecision:
        return PairDecision(a, b, method, name, address, combined, decision, evidence)

    if a.vat and a.vat == b.vat:
        return result("EXACT_VAT", "AUTO_MERGE", f"VAT number {a.vat} on both records")
    shared = a.ibans & b.ibans
    vat_conflict = bool(a.vat and b.vat and a.vat != b.vat)
    if vat_conflict:
        if combined >= REVIEW_SCORE:
            return result(
                "VAT_CONFLICT", "STEWARD_REVIEW", f"Similar records but different VAT: {a.vat} and {b.vat}"
            )
        if shared:
            return result(
                "SHARED_BANK", "STEWARD_REVIEW", "Same bank account, different VAT numbers and names"
            )
        return None
    if shared:
        if name >= BANK_NAME_FLOOR:
            return result("EXACT_BANK", "AUTO_MERGE", "Same bank account and a similar name")
        return result("SHARED_BANK", "STEWARD_REVIEW", "Same bank account, unrelated names")
    if combined >= AUTO_MERGE_SCORE:
        return result("FUZZY", "AUTO_MERGE", f"Combined score {combined}")
    if combined >= REVIEW_SCORE:
        return result("FUZZY", "STEWARD_REVIEW", f"Combined score {combined}")
    return None


def _load(conn: psycopg.Connection) -> list[Record]:
    banks: dict[tuple[str, str], set[str]] = {}
    for row in conn.execute(
        "select source_system, source_supplier_id, account_number from core.supplier_bank_account where not is_deleted"
    ).fetchall():
        banks.setdefault((row["source_system"], row["source_supplier_id"]), set()).add(row["account_number"])
    return [
        Record(
            row["source_system"],
            row["source_supplier_id"],
            standardise_name(row["supplier_name"]),
            row["supplier_name"],
            standardise_address(row["address_line"], row["city"], row["postcode"]),
            standardise_vat(row["vat_number"]),
            row["country_code"],
            banks.get((row["source_system"], row["source_supplier_id"]), set()),
            row["source_changed_at"],
        )
        for row in conn.execute(
            "select * from core.supplier where not is_deleted order by source_system, source_supplier_id"
        ).fetchall()
    ]


def _steward_decisions(conn: psycopg.Connection) -> dict[tuple, str]:
    rows = conn.execute("""
        select distinct on (a_source_system, a_source_supplier_id, b_source_system, b_source_supplier_id)
               a_source_system, a_source_supplier_id, b_source_system, b_source_supplier_id, decision
        from mdm.steward_decision
        order by a_source_system, a_source_supplier_id, b_source_system, b_source_supplier_id, decision_id desc
    """).fetchall()
    return {
        (
            (r["a_source_system"], r["a_source_supplier_id"]),
            (r["b_source_system"], r["b_source_supplier_id"]),
        ): r["decision"]
        for r in rows
    }


def resolve_suppliers() -> dict:
    with pipeline_run("resolve_suppliers") as run, connect() as conn:
        records = _load(conn)
        steward = _steward_decisions(conn)
        by_key = {record.key: record for record in records}

        decisions: list[PairDecision] = []
        for a, b in combinations(sorted(records, key=lambda r: r.key), 2):
            ruling = steward.get((a.key, b.key))
            pair = decide(a, b)
            if ruling:
                name, address, combined = score(a, b)
                method = pair.method if pair else "STEWARD"
                decisions.append(
                    PairDecision(
                        a, b, method, name, address, combined, f"STEWARD_{ruling}", "Steward decision"
                    )
                )
            elif pair:
                decisions.append(pair)

        # Union-find over merging pairs: merges are transitive, so A=B and B=C put all three in one golden record.
        parent = {key: key for key in by_key}

        def find(key: tuple[str, str]) -> tuple[str, str]:
            while parent[key] != key:
                parent[key] = parent[parent[key]]
                key = parent[key]
            return key

        merging = [p for p in decisions if p.decision in ("AUTO_MERGE", "STEWARD_MERGE")]
        for pair in merging:
            parent[find(pair.b.key)] = find(pair.a.key)

        clusters: dict[tuple[str, str], list[Record]] = {}
        for record in records:
            clusters.setdefault(find(record.key), []).append(record)

        conn.execute("delete from mdm.supplier_xref")
        conn.execute("delete from mdm.supplier_golden")
        conn.execute("delete from mdm.supplier_match_candidate")

        strength = {"STEWARD_MERGE": 0, "AUTO_MERGE": 1}
        for members in clusters.values():
            members.sort(key=lambda r: r.key)
            gid = "GS-" + sha256("|".join(members[0].key).encode()).hexdigest()[:10].upper()
            # Survivorship: the most recently changed record's name, the first VAT number found. Good enough to label
            # a cluster; source records keep their own values for anything that matters, such as bank details.
            newest = max(members, key=lambda r: (r.changed_at, r.key))
            vat = next((m.vat for m in members if m.vat), None)
            conn.execute(
                "insert into mdm.supplier_golden (supplier_gid, golden_name, vat_number, country_code, member_count, run_id) "
                "values (%s, %s, %s, %s, %s, %s)",
                (gid, newest.raw_name, vat, newest.country, len(members), run.run_id),
            )
            member_keys = {m.key for m in members}
            for member in members:
                evidence = sorted(
                    (
                        p
                        for p in merging
                        if member.key in (p.a.key, p.b.key) and {p.a.key, p.b.key} <= member_keys
                    ),
                    key=lambda p: (strength[p.decision], -p.combined),
                )
                top = evidence[0] if evidence else None
                method = (
                    "SINGLETON"
                    if top is None
                    else "STEWARD"
                    if top.decision == "STEWARD_MERGE"
                    else top.method
                )
                conn.execute(
                    "insert into mdm.supplier_xref (source_system, source_supplier_id, supplier_gid, match_method, match_score, run_id) "
                    "values (%s, %s, %s, %s, %s, %s)",
                    (*member.key, gid, method, top.combined if top else None, run.run_id),
                )

        for pair in decisions:
            conn.execute(
                "insert into mdm.supplier_match_candidate (a_source_system, a_source_supplier_id, b_source_system, b_source_supplier_id, "
                "method, name_score, address_score, combined_score, decision, evidence, run_id) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    *pair.a.key,
                    *pair.b.key,
                    pair.method,
                    pair.name_score,
                    pair.address_score,
                    pair.combined,
                    pair.decision,
                    pair.evidence,
                    run.run_id,
                ),
            )

        run.lineage(conn, "core.supplier", "mdm.supplier_xref", len(records))
        run.lineage(conn, "core.supplier", "mdm.supplier_golden", len(clusters))
        run.counts = {
            "suppliers": len(records),
            "golden": len(clusters),
            "auto_merged_pairs": sum(1 for p in decisions if p.decision == "AUTO_MERGE"),
            "steward_queue": sum(1 for p in decisions if p.decision == "STEWARD_REVIEW"),
        }
        return run.counts


def record_steward_decision(
    key_a: tuple[str, str], key_b: tuple[str, str], decision: str, decided_by: str
) -> None:
    a, b = sorted((key_a, key_b))
    with connect() as conn:
        conn.execute(
            "insert into mdm.steward_decision (a_source_system, a_source_supplier_id, b_source_system, b_source_supplier_id, decision, decided_by) "
            "values (%s, %s, %s, %s, %s, %s)",
            (*a, *b, decision, decided_by),
        )
