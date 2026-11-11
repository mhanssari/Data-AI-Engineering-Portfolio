"""Planted defects: named records with a stated expected outcome.

Planted records use their own id ranges (members M9xxxx, transactions T9xxxxxx, events E9xxxxxxx, entitlements
R9xxxxxx) and receive only the records listed here, so their expected outcomes do not depend on the random
population. The background population is generated cleanly; any other anomaly in the marts is a model defect.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    code: str
    title: str
    records: tuple[str, ...]
    expected: str


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "D1",
        "Transaction delivered twice, identical",
        ("T9000001", "T9000002"),
        "T9000001 appears twice in batch 1; T9000002 in batch 1 and again in batch 2. Each is kept once.",
    ),
    Scenario(
        "D2",
        "Loyalty event delivered twice",
        ("E900000001", "E900000002"),
        "E900000001 appears twice in batch 1; E900000002 in batch 1 and again in batch 2. Each is kept once.",
    ),
    Scenario(
        "D3",
        "One purchase with earn, bonus and redemption events",
        ("M90001", "T9000003", "R9000001"),
        "Three events reference T9000003; its net sales are counted once (£40.00).",
    ),
    Scenario(
        "D4",
        "Partial return in batch 1, rest of the sale returned in batch 2",
        ("T9000004", "T9000005", "T9000006"),
        "Sale £60.00 on 2026-08-20; return −£15.00 on 2026-09-01 and −£30.00 on 2026-09-22. "
        "Net by attributed date: £15.00 in August 2026.",
    ),
    Scenario(
        "D5",
        "Return whose original sale is not in the extract",
        ("T9000007",),
        "Kept, flagged unmatched, −£12.00 attributed to its own date 2026-02-14.",
    ),
    Scenario(
        "D6",
        "More units returned than sold",
        ("T9000008", "T9000009"),
        "Sale of 2 units, return of 3. Kept and flagged as an over-return.",
    ),
    Scenario(
        "D7",
        "Anonymous transaction",
        ("T9000010",),
        "Counted in net sales; no member; excluded from member metrics.",
    ),
    Scenario(
        "D8",
        "Member who enrols and never buys",
        ("M90002",),
        "Enrolled 2025-01-15. One member-month row per month to the as-of month; eligible from 2025-04-15; "
        "inactive at every month end from April 2025.",
    ),
    Scenario(
        "D9",
        "Store format change",
        ("T9000011", "T9000012"),
        "S003 changes from high_street to retail_park on 2025-09-01. T9000011 (2025-08-31) uses the "
        "high_street version, T9000012 (2025-09-01) the retail_park version.",
    ),
    Scenario(
        "D10",
        "Product sold before its reference data arrives",
        ("P061", "T9000013"),
        "After batch 1, P061 is an inferred product. After batch 2, it has its name and category.",
    ),
    Scenario(
        "D11",
        "Member attached to a transaction after the sale",
        ("T9000014", "M90003"),
        "Anonymous in batch 1. Batch 2 re-delivers the header with M90003. After batch 2 the sale belongs to "
        "M90003, in an incremental build and a full refresh alike.",
    ),
    Scenario(
        "D12",
        "Member enrolment date corrected",
        ("M90004",),
        "Enrolled 2025-03-10 in batch 1, corrected to 2025-02-24 in batch 2. The enrolment cohort moves from "
        "March to February 2025; the snapshot keeps both versions.",
    ),
    Scenario(
        "D13",
        "Entitlement status disagrees with the event log",
        ("R9000002", "M90005"),
        "Status still issued, but a redemption event exists. Resolved as redeemed and flagged.",
    ),
    Scenario(
        "D14",
        "Entitlement stuck in issued after expiry",
        ("R9000003", "M90005"),
        "Issued 2025-11-01, expired 2025-12-01, status still issued. Resolved as expired.",
    ),
    Scenario(
        "D15",
        "Member enrolled days before the as-of date",
        ("M90006", "T9000015"),
        "Enrolled 2026-09-25, first purchase 2026-09-26. Repeat-purchase cohort September 2026 is immature; "
        "not eligible for inactivity.",
    ),
)
