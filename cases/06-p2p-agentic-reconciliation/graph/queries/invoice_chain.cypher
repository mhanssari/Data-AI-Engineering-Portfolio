// Full document chain for one invoice: invoice -> lines -> PO lines -> receipts, requisition, approvals, golden supplier.
// Parameters: $source_system, $invoice_id. OPTIONAL throughout, so a break in the chain shows as a null rather than a missing row.
MATCH (inv:SupplierInvoice {sourceSystem: $source_system, sourceKey: $invoice_id})-[:HAS_LINE]->(line:InvoiceLine)
OPTIONAL MATCH (inv)-[:ISSUED_BY]->(:Supplier)-[:RESOLVES_TO]->(golden:GoldenSupplier)
OPTIONAL MATCH (line)-[:INVOICED_AGAINST]->(poLine:POLine)<-[:HAS_LINE]-(po:PurchaseOrder)
OPTIONAL MATCH (grnLine:GRNLine)-[:RECEIVED_AGAINST]->(poLine)
OPTIONAL MATCH (grn:GoodsReceipt)-[:HAS_LINE]->(grnLine)
OPTIONAL MATCH (pr:PurchaseRequisition)-[:CONVERTED_TO]->(po)
OPTIONAL MATCH (pr)-[:HAS_APPROVAL]->(:Approval)-[:APPROVED_BY]->(approver:Employee)
OPTIONAL MATCH (result:MatchResult)-[:ASSESSES]->(line)
OPTIONAL MATCH (result)-[:RAISES]->(exception:ReconException)
RETURN inv.documentNumber AS invoice_number,
       line.lineNumber AS line_number,
       golden.partyName AS golden_supplier,
       po.documentNumber AS po_number,
       collect(DISTINCT grn.documentNumber) AS grn_numbers,
       collect(DISTINCT pr.documentNumber) AS pr_numbers,
       collect(DISTINCT approver.partyName) AS approvers,
       result.hasMatchStatus AS match_status,
       collect(DISTINCT exception.hasReason) AS reasons
ORDER BY line_number
