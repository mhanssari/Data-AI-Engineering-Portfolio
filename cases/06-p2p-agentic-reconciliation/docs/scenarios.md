# Planted scenarios and expected outcomes

The generator creates these deliberately and writes [`data/samples/expected_outcomes.csv`](../data/samples/expected_outcomes.csv), one row per document or line. `tests/test_scenarios.py`, `test_incremental.py` and `test_mdm.py` assert every row. Status and reason sets are compared exactly, so a rule that over-fires fails as well as one that misses.

Everything else in the data is clean by construction, so any exception outside these scenarios is a false positive, and a test says so. Reason codes are concepts in [`ontology/vocab.ttl`](../ontology/vocab.ttl); parameters are in [`data/reference/p2p_policy.yaml`](../data/reference/p2p_policy.yaml).

| ID | Scenario | Company | Expected | Reason code | Why it is here |
| --- | --- | --- | --- | --- | --- |
| S01 | Perfect three-way match | GB01 | Matched | — | Baseline; also validated through SHACL |
| S02 | Price 1.5% over | DE01 | MatchedWithinTolerance | — | Default 2% tolerance applied |
| S03 | Price 5% over | GB01 | Exception | PriceVariance | |
| S04 | Invoiced 120, received 100 | DE01 | Exception | QuantityVariance | |
| S05 | Invoice dated before the receipt | GB01 | Exception | InvoiceBeforeReceipt | |
| S06 | Partial receipt, matching partial invoice | DE01 | Matched | — | Cumulative quantities, not PO totals |
| S07 | Same invoice with a reformatted number | GB01 | Original Matched, later one Exception | DuplicateInvoice | Number normalisation |
| S08 | Invoice with no PO | DE01 | Exception | NoPurchaseOrder | |
| S09 | PO dated after the invoice | GB01 | Exception | RetrospectivePO | |
| S10 | Three requisitions of 4,000 against a 5,000 tier, same requester and supplier, within 7 days | DE01 | Breach on each, invoices Exception | SplitRequisition | All members flagged |
| S11 | 12,000 approved by a 5,000 manager | GB01 | Breach, invoice Exception | ApprovalLimitExceeded | Breach propagates to the invoice |
| S12 | Requester approved their own requisition | DE01 | Breach, invoice Exception | SegregationOfDuties | Also found independently by SHACL |
| S13 | Eight supplier pairs across ERPs (see below) | Both | Merged, StewardReview or NotMerged | Method | Entity resolution |
| S14 | Bank account changed 10 days before an invoice; an earlier invoice from the same supplier | GB01 | Earlier Matched, later Exception | BankDetailChange | Window is relative to each invoice |
| S15 | GBP invoice on a EUR PO | DE01 | Exception | CurrencyMismatch | Price is not assessed across currencies |
| S16 | Credit note against an invoice | GB01 | Matched; GRNI 20 units | — | SAP positive amount becomes negative |
| S17 | Receipt keyed short, corrected in batch 2 | DE01 | QuantityVariance, then Matched | — | Late correction |
| S18 | Service invoice | GB01 | Matched | — | Two-way match |
| S19 | Goods received, never invoiced | DE01 | In GRNI: 70 units, 875.00 | — | |
| S20 | Whole chain arrives before its supplier master | DE01 | Quarantined, then Matched | — | Late reference data; a chain clears in one run |
| S21 | One invoice sent to both ERPs | Both | First Matched, second Exception | DuplicateInvoice | Only the golden supplier links them. Also proves FX: 5,500 EUR is within a 5,000 GBP limit |
| S22 | Credit note larger than its invoice | DE01 | Exception; no GRNI | UnmatchedCreditNote | Oracle negative amounts |
| S23 | Contract tolerance: 1% to March, 5% from April | GB01 | 1.5% in February Exception, 4% in May within tolerance | PriceVariance | Contract overrides default, as of the invoice date |
| S24 | Converted with no approval | DE01 | Breach, invoice Exception | MissingApproval | Oracle's SUBMIT row is not an approval |
| S25 | PO line with no requisition | GB01 | Exception | MissingRequisition | |
| S26 | Limit raised after the approval | DE01 | Breach, invoice Exception | ApprovalLimitExceeded | Reading today's limit would wrongly pass |
| S27 | Older version of a PO line redelivered out of order | GB01 | Newer version kept | — | Upsert if newer |
| S28 | Requisition cancelled by a CDC delete | GB01 | Soft-deleted | — | |
| S29 | Late delivery stamped before the high watermark | DE01 | QuantityVariance, then Matched | — | Only the lookback catches it |
| S30 | Goods invoiced, nothing received | GB01 | PendingReceipt | — | Not a variance: nothing to disagree with yet |
| S31 | New chain in the change log, after the cut-off | GB01 | Matched, not in GRNI | — | Incremental load; cut-off respected |
| S32 | Genuine invoice after a held duplicate | GB01 | Held duplicate Exception, others Matched | DuplicateInvoice | A held duplicate does not consume quantity |
| S33 | Part invoiced plus a duplicate of that part | DE01 | Duplicate Exception; GRNI 50 units | DuplicateInvoice | A held duplicate does not reduce the accrual |
| S34 | First bank account 19 days before an invoice | GB01 | Matched | — | Onboarding is not a change |
| S35 | Split group, one member later cancelled | DE01 | Breach, then none | SplitRequisition | Deleted documents leave the controls |
| S36 | One penny over | GB01 | Matched | — | Rounding is an exact match, not a tolerance match |

## S13 supplier pairs

| Pair | Evidence | Expected |
| --- | --- | --- |
| Brightwell Packaging | Same VAT, formatted differently; name variant | Merged, EXACT_VAT |
| Corvid Ingredients | Same VAT; different address | Merged, EXACT_VAT |
| Rheintal Logistik | Same bank account; similar name; one VAT missing | Merged, EXACT_BANK |
| Kestrel Industrial Supplies | No VAT or bank evidence; same name and address | Merged, FUZZY |
| Oakfield Ingredients / Oakfield Food Ingredients | Similar name; different address | StewardReview, FUZZY |
| Atlas Chemicals | Same name and address; conflicting VAT | StewardReview, VAT_CONFLICT |
| Northern Packaging / Northern Paper Handel | Different VAT, similar first word | NotMerged |
| Tern Consulting / Marlow Reinigung | Same bank account; unrelated names and VAT | StewardReview, SHARED_BANK (fraud signal) |

## Traps in the clean data

The background also holds things a careless rule would trip over: Oracle SUBMIT rows by the preparer in the approval history, bank changes well outside the window, consolidated POs with lines from two requisitions, two-instalment invoices, and underpriced or penny-rounded invoices.
