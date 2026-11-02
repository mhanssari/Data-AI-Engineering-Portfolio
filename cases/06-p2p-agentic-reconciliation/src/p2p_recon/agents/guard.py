"""The last check before a proposal is stored. It trusts SQL and the run's own tool results, never the model.

This module is why a misbehaving model cannot produce a false approval: whatever the draft says, the decision to allow
AUTO_APPROVE comes from facts read back from recon.
"""

import re
from dataclasses import dataclass, field

from p2p_recon.agents.providers import ACTIONS

CLEAN_STATUSES = ("Matched", "MatchedWithinTolerance")
FALLBACK_ACTION = "NEEDS_HUMAN_REVIEW"
NUMBER = re.compile(r"\d[\d,]*\.\d+|\d{3,}")


@dataclass
class GuardResult:
    action: str
    explanation: str
    citations: list[str]
    flags: list[str] = field(default_factory=list)


def _unsupported_numbers(explanation: str, evidence_text: str) -> list[str]:
    """Numbers in the explanation that no tool returned: the model may repeat figures but not compute new ones."""
    return sorted({n for n in NUMBER.findall(explanation) if n.replace(",", "") not in evidence_text})


def apply_guard(
    drafted_action: str | None,
    explanation: str | None,
    cited: list[str],
    seen_citations: list[str],
    fallback_citations: list[str],
    status: str,
    reasons: list[str],
    breaches: list[dict],
    evidence_text: str,
) -> GuardResult:
    flags: list[str] = []
    action = drafted_action
    text = (explanation or "").strip()

    if action not in ACTIONS:
        if action is not None:
            flags.append(f"invalid_action:{action}")
        action = FALLBACK_ACTION

    seen = set(seen_citations)
    citations = [c for c in dict.fromkeys(cited) if c in seen]
    invented = [c for c in cited if c not in seen]
    if invented:
        # A citation the run never retrieved is either invented or something the user may not read.
        flags.append(f"unverified_citations:{len(invented)}")
    if not citations:
        flags.append("uncited")
        action = FALLBACK_ACTION
        citations = list(fallback_citations)

    clean = status in CLEAN_STATUSES and not reasons and not breaches
    if action == "AUTO_APPROVE" and (not clean or invented):
        flags.append("auto_approve_blocked")
        action = FALLBACK_ACTION

    unsupported = _unsupported_numbers(text, evidence_text)
    if unsupported:
        flags.append(f"unsupported_numbers:{','.join(unsupported)}")
        text = "The drafted explanation quoted figures not found in the evidence and was withheld."
    if not text:
        flags.append("no_explanation")
        action = FALLBACK_ACTION if action == "AUTO_APPROVE" else action
        text = "The model gave no explanation. See the cited findings."
    return GuardResult(action, text, citations, flags)
