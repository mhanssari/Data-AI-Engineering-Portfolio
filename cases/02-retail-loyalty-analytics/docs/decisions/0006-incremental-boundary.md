# 0006 The incremental boundary of `fct_sales_line`

Status: accepted

Date: 11 November 2026

## Context

`fct_sales_line` is the largest table and the one that grows every day, so it is the one worth building incrementally. Three kinds of late material change rows that were already built: a header corrected after the sale (a member attached), a return arriving weeks after its sale, and reference data that changes which store version a past sale belongs to. An incremental run that misses any of them leaves the table different from a full rebuild, silently.

## Options

1. **Date window with lookback** (reprocess the last *n* days by transaction date). The common default. Misses anything later than the lookback, and a late return carries its own date, not the sale's.
2. **Load watermark on lines only.** Catches new lines whatever their date. Misses a corrected header whose lines were not re-sent, and anything driven by reference data.
3. **Load watermark on lines and headers, plus the dependants of what changed.** Reprocess every transaction whose header or lines arrived since the last build; every return of an affected sale, or of the same sale as an affected return; and every existing row whose store version no longer matches `dim_store`.

## Decision and rationale

Option 3.

- **Watermark:** `source_load_id`, the loader's sequence number, takes the place of time. It rises with every file loaded and does not depend on a clock.
- **Replacement unit:** the transaction. `delete+insert` on `transaction_id` replaces all of a transaction's lines, so a line removed by a correction also disappears. A `merge` on the line key would leave it behind.
- **Store repair:** a cheap join of the existing fact's keys to `dim_store`. Without it, a store version arriving late would leave past sales on the old version.

## Consequences and validation

- After batch 2 the incremental run rewrote 2,818 of 118,376 lines:
  - the new batch 2 lines;
  - the late member claim (D11);
  - an identical re-delivery (D1);
  - four earlier returns that share a sale with a new return.
- Tests compare the incremental table with a full refresh after batch 2, after a store version arrives late, and after a sale arrives after its return. Every column must match except the one recording which run wrote the row. A rerun with nothing new rewrites no row.
- The store-repair join and the dependants lookup scan the whole fact's keys. That is cheap at 118,000 rows. At scale, both would be limited to the partitions (transaction months) touched by the changed rows.
- A deleted transaction at source is not handled: the extracts have no delete marker. A full refresh is the documented remedy.
