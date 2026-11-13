# 0002 Money in integer minor units, one currency at a time

Status: accepted

Date: 12 November 2026

## Context

Reconciliation compares amounts that must agree to the penny, in three currencies, with fees computed from basis points. Floating point cannot represent most decimal amounts exactly; a total across currencies has no meaning.

## Options

1. **Decimal amounts (`numeric(18,2)`) with a currency column.** Exact, but two decimals do not fit every currency, and rounding can creep in through division.
2. **Integer minor units (`bigint`) with an ISO currency code.** Exact and fast, with rounding confined to one place.

## Decision and rationale

Option 2. Every amount is an integer of minor units next to its currency. The one rounding rule is in the expected fee: basis points × gross ÷ 10,000, rounded half up, plus a fixed fee. No query, view or output adds amounts with different currencies; foreign exchange is out of scope.

## Consequences and validation

- Invariant 5 is tested: no aggregate groups amounts without the currency.
- Display formatting (dividing by 100) happens only at the edge, in views meant for people.
- A currency without two minor digits (for example JPY) would need its exponent from reference data. None is used here.
