# Metric catalogue

Status: definitions. GRNI and value on hold are calculated today (`recon.current_grni_line`, `p2p status`). Exception counts by reason are available from `recon.current_exception`. The rest are not calculated yet; agent and retrieval metrics need milestone 2. Tolerances and windows are in [`p2p_policy.yaml`](../data/reference/p2p_policy.yaml).

| Metric | Business question | Owner | Formula | Grain / time basis | Unit | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| First-time match rate | How many invoice lines match without intervention? | AP manager | Lines with status Matched or Matched within tolerance on first run ÷ all invoice lines received in period | Invoice line; invoice received date | % | Zero denominator shown as "no invoices" |
| Exception rate by reason | Where do invoices fail? | AP manager | Exceptions with reason r ÷ all invoice lines in period | Invoice line × reason | % | A line can carry several reasons; totals across reasons can exceed 100% |
| Value on hold | How much spend is blocked? | Controller | Sum of invoice line net amount with status Exception or PendingReceipt | Invoice line; snapshot date | Currency, per currency | Never summed across currencies without a rate table |
| Duplicate value prevented | What did duplicate detection stop? | Controller | Sum of net amount of invoices flagged DuplicateInvoice and not overridden | Invoice; period | Currency | Synthetic; not a real saving |
| GRNI | What has been received but not invoiced at cut-off? | Controller | Sum over goods PO lines of (received qty − invoiced qty) × PO unit price, where positive; receipts and invoices dated on or before the cut-off | PO line; cut-off date | Currency, per currency | Held duplicates and rejected credit notes are ignored. A valid credit note raises GRNI until a return is posted; returns are not modelled. Document dates, not knowledge time |
| Control breaches | How often are approval controls broken? | Internal audit | Count of exceptions with reasons ApprovalLimitExceeded, SegregationOfDuties, SplitRequisition, RetrospectivePO | Requisition / PO; period | Count | |
| PR-to-PO cycle time | How fast do requests become orders? | Procurement lead | Median days from PR creation to PO creation | PO; PO date | Days | Excludes cancelled PRs |
| Agent outcome accuracy | Do agents reach the right conclusion? | Case owner | Proposals whose status and reason match expected outcomes ÷ evaluated scenarios | Scenario; evaluation run | % | From the evaluation harness |
| False auto-approvals | Did any agent approve what it should not? | Case owner | Count of proposals recommending release where expected status is Exception | Scenario; evaluation run | Count | Target 0 |
| Retrieval recall@k | Is the right evidence found? | Case owner | Questions with at least one expected chunk in top k ÷ questions | Question; evaluation run | % | Compared across retrieval methods |
