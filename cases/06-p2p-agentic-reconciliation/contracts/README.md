# Data contracts

What each data product promises its consumers, checked against the live database by `uv run p2p contracts-check` and `tests/test_contracts.py`.

| Contract | Tables | Owner |
| --- | --- | --- |
| [supplier](supplier.yaml) | `core.supplier`, `mdm.supplier_golden`, `mdm.supplier_xref` | Master data steward |
| [purchase_order](purchase_order.yaml) | `core.purchase_order`, `core.po_line` | Procurement lead |
| [invoice](invoice.yaml) | `core.supplier_invoice`, `core.invoice_line` | Accounts payable manager |
| [match_result](match_result.yaml) | `recon.match_result`, `recon.exception` | Financial controller |

The owners are roles in the fictional group. Each contract holds schema, grain, keys, quality thresholds, a freshness SLA, access rules and a version.

## What is checked

- **Shape:** the file is complete, and its version is `MAJOR.MINOR.PATCH`.
- **Schema:** columns, types, nullability and primary key match the database. A column that exists but is not declared also fails, because it has no promise behind it.
- **Quality:** row counts, null rates, accepted values and non-negative amounts.
- **Access:** the roles listed can read each table, `agent_service` cannot, and row-level security is on.
- **Freshness:** hours since the source extract, against the SLA. The synthetic data has fixed dates, so the check runs against an injected clock. **The 48-hour SLA is a proposal, not a measured service level.**

## Changing a contract

`released/` holds the last released version of each contract. Comparing against it:

| Change | Rule |
| --- | --- |
| Removing a table or column, changing a type, key or grain, making a column nullable, weakening a threshold, removing a check, accepting a new value, or slowing the SLA | Breaking: needs a new major version and notice to consumers |
| Adding a column, tightening a threshold or SLA | Needs a new version, but not a major one |

After bumping the version, copy the file to `released/` as part of the release. A test fails on a breaking change without a major version, and on any change at the same version.
