# Trace of proposal 452

**Invoice line:** SAP_LIKE 5100000009 line 1, company GB01.
**SQL says:** Exception, reasons BankDetailChange.
**Agent run:** 11, provider `offline`, on behalf of `ap_analyst_uk`, reading recon run 9.
**Drafted:** VERIFY_BANK_DETAILS. **Stored:** VERIFY_BANK_DETAILS. **Guard flags:** none.

> SQL holds this line as Exception (BankDetailChange: {"changed_on": "2026-05-10"}). Proposed action: VERIFY_BANK_DETAILS. Relevant rule and correspondence are cited.

## 1. What the agents did

| Step | Agent | Tool calls | Latency ms |
| --- | --- | --- | --- |
| 2131 | supervisor | read_queue_item (ok) | 17.6 |
| 2132 | controls | check_controls (ok) | 20.4 |
| 2133 | matching | get_match_result (ok), get_contract_terms (ok) | 34.2 |
| 2134 | investigator | get_document_chain (ok) | 19.6 |
| 2135 | investigator | – | 4.1 |
| 2136 | investigator | search_documents (ok) | 115.1 |
| 2137 | investigator | – | 4.5 |
| 2138 | guard | – | 32.9 |

## 2. Rows cited

| Table | Key |
| --- | --- |
| recon.match_result | `{"invoice_id": "5100000009", "line_number": 1, "run_id": 9, "source_system": "SAP_LIKE"}` |
| recon.exception | `{"invoice_id": "5100000009", "line_number": 1, "reason_code": "BankDetailChange", "run_id": 9, "source_system": "SAP_LIKE"}` |

## 3. Documents cited

| Chunk | File | SHA-256 of the file | Text |
| --- | --- | --- | --- |
| policy_p2p:bank-change-window-days | policy_p2p.md | `3d1954aa916d1618…` | - **bank_change_window_days** (30 days): An invoice is held for verification if the supplier's bank account changed in the 30 days up to and including the invoice date. |
| procedure_GB01:verifying-a-bank-change | procedure_GB01.md | `04e53ad8a49b7ff8…` | Telephone the supplier on the number already on file, never one from the request itself. The treasury assistant countersigns the call record before the invoice is released. |
| email_S14_bank_change:overview | email_S14_bank_change.md | `a7878b605e7e38be…` | From: accounts@icl.example Date: 9 May 2026 Subject: New bank details  Please update our bank account with immediate effect for all open invoices. Supplier reference 0000100015. We are unable to take calls this week, so please reply by email only. |

## 4. Source rows behind the invoice line

From each `core` row through `source_raw_id` to the delivered file. Where a row was corrected, this is the version in force.

| Core table | Raw row | Source file | File row | Batch | CDC | File checksum |
| --- | --- | --- | --- | --- | --- | --- |
| core.invoice_line | raw.sap_invoice_item #1928 | sap/sap_invoice_item.csv | 9 | batch_001 | – | `91ade0840ad0d528…` |
| core.supplier_invoice | raw.sap_invoice_header #1766 | sap/sap_invoice_header.csv | 9 | batch_001 | – | `e43e50f913cd409f…` |
| core.supplier | raw.sap_supplier #2879 | sap/sap_supplier.csv | 15 | batch_001 | – | `28dfd602bfbda1e5…` |
| core.po_line | raw.sap_po_item #2274 | sap/sap_po_item.csv | 7 | batch_001 | – | `dcff57629a3c82be…` |
| core.purchase_order | raw.sap_po_header #2111 | sap/sap_po_header.csv | 7 | batch_001 | – | `0eb89eabe23bee5a…` |
| core.grn_line | raw.sap_goods_receipt #1594 | sap/sap_goods_receipt.csv | 7 | batch_001 | – | `398b3f98c8d927ba…` |
| core.grn_line | raw.sap_goods_receipt #1595 | sap/sap_goods_receipt.csv | 8 | batch_001 | – | `398b3f98c8d927ba…` |
| core.purchase_requisition | raw.sap_pr #2463 | sap/sap_pr.csv | 7 | batch_001 | – | `6bbd9c76fd4b9b2d…` |
| core.approval | raw.sap_pr_approval #2667 | sap/sap_pr_approval.csv | 7 | batch_001 | – | `6902e5a5fefee6bc…` |

## 5. Dataset lineage

From `ops.lineage_edge`, newest run of each edge, nearest to the proposal first.

| From | To | Latest run |
| --- | --- | --- |
| core.contract | agent.proposal | 16 |
| docs.chunk | agent.proposal | 16 |
| neo4j | agent.proposal | 16 |
| recon.control_breach | agent.proposal | 16 |
| recon.exception | agent.proposal | 16 |
| recon.match_result | agent.proposal | 16 |
| stg.contract | core.contract | 7 |
| data/generated/documents | docs.chunk | 11 |
| core+mdm+recon | neo4j | 12 |
| core.purchase_requisition | recon.control_breach | 9 |
| recon.match_result | recon.exception | 9 |
| core.approval | recon.match_result | 9 |
| core.contract | recon.match_result | 9 |
| core.employee | recon.match_result | 9 |
| core.fx_rate | recon.match_result | 9 |
| core.goods_receipt | recon.match_result | 9 |
| core.grn_line | recon.match_result | 9 |
| core.invoice_line | recon.match_result | 9 |
| core.po_line | recon.match_result | 9 |
| core.policy_parameter | recon.match_result | 9 |
| core.purchase_order | recon.match_result | 9 |
| core.purchase_requisition | recon.match_result | 9 |
| core.supplier_bank_account | recon.match_result | 9 |
| core.supplier_invoice | recon.match_result | 9 |
| mdm.supplier_xref | recon.match_result | 9 |
| raw.group_contracts | stg.contract | 7 |
| stg.purchase_requisition | core.purchase_requisition | 7 |
| stg.approval | core.approval | 7 |
| stg.employee | core.employee | 7 |
| stg.fx_rate | core.fx_rate | 7 |
| stg.goods_receipt | core.goods_receipt | 7 |
| stg.grn_line | core.grn_line | 7 |
| stg.invoice_line | core.invoice_line | 7 |
| stg.po_line | core.po_line | 7 |
| stg.policy_parameter | core.policy_parameter | 7 |
| stg.purchase_order | core.purchase_order | 7 |
| stg.supplier_bank_account | core.supplier_bank_account | 7 |
| stg.supplier_invoice | core.supplier_invoice | 7 |
| core.supplier | mdm.supplier_xref | 8 |
| file:batch_001/group/group_contracts.csv | raw.group_contracts | 1 |
| raw.ora_requisitions | stg.purchase_requisition | 7 |
| raw.sap_pr | stg.purchase_requisition | 7 |
| raw.ora_approval_history | stg.approval | 7 |
| raw.sap_pr_approval | stg.approval | 7 |
| raw.ora_employees | stg.employee | 7 |
| raw.sap_employee | stg.employee | 7 |
| raw.group_fx_rates | stg.fx_rate | 7 |
| raw.ora_receipts | stg.goods_receipt | 7 |
| raw.sap_goods_receipt | stg.goods_receipt | 7 |
| raw.ora_receipts | stg.grn_line | 7 |
| raw.sap_goods_receipt | stg.grn_line | 7 |
| raw.ora_invoice_lines | stg.invoice_line | 7 |
| raw.sap_invoice_header | stg.invoice_line | 7 |
| raw.sap_invoice_item | stg.invoice_line | 7 |
| raw.ora_po_headers | stg.po_line | 7 |
| raw.ora_po_lines | stg.po_line | 7 |
| raw.sap_po_header | stg.po_line | 7 |
| raw.sap_po_item | stg.po_line | 7 |
| raw.group_policy_parameters | stg.policy_parameter | 7 |
| raw.ora_po_headers | stg.purchase_order | 7 |
| raw.sap_po_header | stg.purchase_order | 7 |
| raw.ora_supplier_bank_accounts | stg.supplier_bank_account | 7 |
| raw.sap_supplier_bank | stg.supplier_bank_account | 7 |
| raw.ora_invoices | stg.supplier_invoice | 7 |
| raw.sap_invoice_header | stg.supplier_invoice | 7 |
| stg.supplier | core.supplier | 7 |
| file:batch_001/oracle/ora_requisitions.csv | raw.ora_requisitions | 1 |
| file:batch_002/oracle/ora_requisitions.csv | raw.ora_requisitions | 6 |
| file:batch_001/sap/sap_pr.csv | raw.sap_pr | 1 |
| file:batch_002/sap/sap_pr.csv | raw.sap_pr | 6 |
| file:batch_001/oracle/ora_approval_history.csv | raw.ora_approval_history | 1 |
| file:batch_001/sap/sap_pr_approval.csv | raw.sap_pr_approval | 1 |
| file:batch_002/sap/sap_pr_approval.csv | raw.sap_pr_approval | 6 |
| file:batch_001/oracle/ora_employees.csv | raw.ora_employees | 1 |
| file:batch_001/sap/sap_employee.csv | raw.sap_employee | 1 |
| file:batch_001/group/group_fx_rates.csv | raw.group_fx_rates | 1 |
| file:batch_001/oracle/ora_receipts.csv | raw.ora_receipts | 1 |
| file:batch_002/oracle/ora_receipts.csv | raw.ora_receipts | 6 |
| file:batch_001/sap/sap_goods_receipt.csv | raw.sap_goods_receipt | 1 |
| file:batch_002/sap/sap_goods_receipt.csv | raw.sap_goods_receipt | 6 |
| file:batch_001/oracle/ora_invoice_lines.csv | raw.ora_invoice_lines | 1 |
| file:batch_001/sap/sap_invoice_header.csv | raw.sap_invoice_header | 1 |
| file:batch_002/sap/sap_invoice_header.csv | raw.sap_invoice_header | 6 |
| file:batch_001/sap/sap_invoice_item.csv | raw.sap_invoice_item | 1 |
| file:batch_002/sap/sap_invoice_item.csv | raw.sap_invoice_item | 6 |
| file:batch_001/oracle/ora_po_headers.csv | raw.ora_po_headers | 1 |
| file:batch_001/oracle/ora_po_lines.csv | raw.ora_po_lines | 1 |
| file:batch_001/sap/sap_po_header.csv | raw.sap_po_header | 1 |
| file:batch_002/sap/sap_po_header.csv | raw.sap_po_header | 6 |
| file:batch_001/sap/sap_po_item.csv | raw.sap_po_item | 1 |
| file:batch_002/sap/sap_po_item.csv | raw.sap_po_item | 6 |
| file:batch_001/group/group_policy_parameters.csv | raw.group_policy_parameters | 1 |
| file:batch_001/oracle/ora_supplier_bank_accounts.csv | raw.ora_supplier_bank_accounts | 1 |
| file:batch_002/oracle/ora_supplier_bank_accounts.csv | raw.ora_supplier_bank_accounts | 6 |
| file:batch_001/sap/sap_supplier_bank.csv | raw.sap_supplier_bank | 1 |
| file:batch_001/oracle/ora_invoices.csv | raw.ora_invoices | 1 |
| raw.ora_supplier_sites | stg.supplier | 7 |
| raw.ora_suppliers | stg.supplier | 7 |
| raw.sap_supplier | stg.supplier | 7 |
| file:batch_001/oracle/ora_supplier_sites.csv | raw.ora_supplier_sites | 1 |
| file:batch_002/oracle/ora_supplier_sites.csv | raw.ora_supplier_sites | 6 |
| file:batch_001/oracle/ora_suppliers.csv | raw.ora_suppliers | 1 |
| file:batch_002/oracle/ora_suppliers.csv | raw.ora_suppliers | 6 |
| file:batch_001/sap/sap_supplier.csv | raw.sap_supplier | 1 |

## 6. Audit log and decisions

| When | Event | Role in use | On behalf of | Detail |
| --- | --- | --- | --- | --- |
| 2026-11-02 18:02:53 | proposal_stored | agent_service | ap_analyst_uk | `{"drafted_action": "VERIFY_BANK_DETAILS", "guard_flags": [], "invoice_id": "5100000009", "line_number": 1, "proposed_action": "VERIFY_BANK_DETAILS", "sql_status": "Exception"}` |
| 2026-11-02 18:06:07 | human_decision | ap_analyst_uk | ap_analyst_uk | `{"decision": "QUERY", "note": ""}` |
