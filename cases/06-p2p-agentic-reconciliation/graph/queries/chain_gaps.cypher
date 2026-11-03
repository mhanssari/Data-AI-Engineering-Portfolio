// Chain-completeness gaps, one row per document and gap type. A gap is not always an error: an invoice with no
// receipt may simply be early. Agreement with the equivalent SQL is checked in tests/test_graph.py.
MATCH (line:InvoiceLine)<-[:HAS_LINE]-(inv:SupplierInvoice)
WHERE NOT (line)-[:INVOICED_AGAINST]->(:POLine)
RETURN 'INVOICE_WITHOUT_PO' AS gap, inv.sourceSystem AS source_system, inv.sourceKey AS document
UNION
MATCH (line:InvoiceLine)-[:INVOICED_AGAINST]->(poLine:POLine {matchType: 'THREE_WAY'})
WHERE NOT ()-[:RECEIVED_AGAINST]->(poLine)
MATCH (inv:SupplierInvoice)-[:HAS_LINE]->(line)
RETURN 'INVOICE_WITHOUT_RECEIPT' AS gap, inv.sourceSystem AS source_system, inv.sourceKey AS document
UNION
MATCH (po:PurchaseOrder)
WHERE NOT ()-[:CONVERTED_TO]->(po)
RETURN 'PO_WITHOUT_REQUISITION' AS gap, po.sourceSystem AS source_system, po.documentNumber AS document
UNION
MATCH (pr:PurchaseRequisition)-[:CONVERTED_TO]->(:PurchaseOrder)
WHERE NOT (pr)-[:HAS_APPROVAL]->()
RETURN 'REQUISITION_WITHOUT_APPROVAL' AS gap, pr.sourceSystem AS source_system, pr.documentNumber AS document
