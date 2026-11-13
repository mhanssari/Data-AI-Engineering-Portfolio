"""Planted defects P1-P16: named records with a stated expected outcome.

Planted payments use ids pay-9000001 upwards and merchants M001 (GBP), M002 (EUR), M003 (USD) and M041 (GBP, sent
late). They receive only the records written for them in generator._plant, so their outcomes do not depend on the
random population, which is generated cleanly.
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
        "P1",
        "Event delivered twice",
        ("pay-9000001", "evt-900000101"),
        "The captured event appears twice in the file for 3 October; counted once.",
    ),
    Scenario(
        "P2",
        "Events out of order across files",
        ("pay-9000002",),
        "Captured (sequence 3) arrives on 3 October, authorised (sequence 2) on 4 October; final state captured.",
    ),
    Scenario(
        "P3",
        "Illegal transition",
        ("pay-9000003", "evt-900000303"),
        "A refund event for a payment never captured; not applied, recorded; the payment is later voided.",
    ),
    Scenario(
        "P4",
        "Malformed event",
        ("pay-9000004", "evt-900000401"),
        "Amount '12,50' in the file for 5 October; quarantined with its reason; the rest of the file loads.",
    ),
    Scenario(
        "P5",
        "Event for a merchant not yet in reference data",
        ("M041", "pay-9000005"),
        "Events on 9 October quarantined; the merchant arrives on 12 October; reprocessed and matched.",
    ),
    Scenario(
        "P6",
        "Captured payment never settled",
        ("pay-9000006",),
        "Captured 6 October; pending on 7 October; missing settlement from the run for 8 October on.",
    ),
    Scenario(
        "P7",
        "Settlement line with no payment",
        ("pay-9000099", "stl-EUR-20261007-P7"),
        "Unexpected settlement exception in the EUR file for 7 October.",
    ),
    Scenario(
        "P8",
        "Partial settlements that sum correctly",
        ("pay-9000008",),
        "Two EUR lines of 60.00 and 40.00 for a 100.00 capture; matched, no exception.",
    ),
    Scenario(
        "P9",
        "Partial settlements that sum short",
        ("pay-9000009",),
        "USD lines of 90.00 and 50.00 for a 150.00 capture; amount mismatch of 10.00.",
    ),
    Scenario(
        "P10",
        "Fee above the price plan",
        ("pay-9000010",),
        "GBP fee 1.50 above the plan; fee mismatch exception.",
    ),
    Scenario(
        "P11",
        "Settled in the wrong currency",
        ("pay-9000011",),
        "A GBP payment settled in the EUR file; currency mismatch; amounts never added across currencies.",
    ),
    Scenario(
        "P12",
        "Settlement file sent twice",
        ("settlement_GBP_2026-10-13.csv",),
        "The same file arrives again on 15 October; skipped, nothing changes.",
    ),
    Scenario(
        "P13",
        "Payment settled twice",
        ("pay-9000013",),
        "EUR lines on 13 and 14 October under different line ids; duplicate settlement exception.",
    ),
    Scenario(
        "P14",
        "Settlement file three days late",
        ("settlement_USD_2026-10-20.csv",),
        "Due on 21 October, arrives on 23 October; its payments are missing in the run for 22 October and matched "
        "in the run for 23 October.",
    ),
    Scenario(
        "P15",
        "Refund after settlement, and a reversal",
        ("pay-9000015", "pay-9000016"),
        "A 30.00 refund line against a 100.00 sale (expected 70.00), and a full reversal (expected 0); both matched.",
    ),
    Scenario(
        "P16",
        "Payment stuck in authorised",
        ("pay-9000017",),
        "Authorised 2 October, never captured, voided or expired; not due for settlement; an ageing authorisation.",
    ),
)
