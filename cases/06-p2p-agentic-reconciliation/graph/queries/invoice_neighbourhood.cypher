// What surrounds one invoice, for retrieval and for the investigator: its supplier, contracts, purchase orders,
// receipts, requisitions and approvers. Neo4j Community has no fine-grained security, so the filter is in the query:
// the invoice must belong to a company the user may see, and the shared golden supplier never leads to another ERP's
// records. It reports only how many such records exist.
// Parameters: $source_system, $invoice_id, $companies, $systems.
MATCH (inv:SupplierInvoice {sourceSystem: $source_system, sourceKey: $invoice_id})-[:BELONGS_TO]->(entity:LegalEntity)
WHERE entity.companyCode IN $companies
OPTIONAL MATCH (inv)-[:ISSUED_BY]->(supplier:Supplier)
OPTIONAL MATCH (supplier)-[:RESOLVES_TO]->(golden:GoldenSupplier)<-[:RESOLVES_TO]-(sibling:Supplier)
WHERE sibling <> supplier
WITH inv, entity, supplier, golden,
     [s IN collect(DISTINCT sibling) WHERE s.sourceSystem IN $systems | s.sourceKey] AS visible_siblings,
     size([s IN collect(DISTINCT sibling) WHERE NOT s.sourceSystem IN $systems]) AS hidden_siblings
OPTIONAL MATCH (inv)-[:HAS_LINE]->(:InvoiceLine)-[:INVOICED_AGAINST]->(poLine:POLine)<-[:HAS_LINE]-(po:PurchaseOrder)
OPTIONAL MATCH (poLine)-[:GOVERNED_BY]->(contract:Contract)
OPTIONAL MATCH (grnLine:GRNLine)-[:RECEIVED_AGAINST]->(poLine)
OPTIONAL MATCH (grn:GoodsReceipt)-[:HAS_LINE]->(grnLine)
OPTIONAL MATCH (pr:PurchaseRequisition)-[:CONVERTED_TO]->(po)
OPTIONAL MATCH (pr)-[:REQUESTED_BY]->(requester:Employee)
OPTIONAL MATCH (pr)-[:HAS_APPROVAL]->(:Approval)-[:APPROVED_BY]->(approver:Employee)
RETURN inv.documentNumber AS invoice_number,
       entity.companyCode AS company_code,
       supplier.sourceSystem AS source_system,
       supplier.sourceKey AS source_supplier_id,
       supplier.partyName AS supplier_name,
       golden.goldenId AS golden_supplier_id,
       visible_siblings,
       hidden_siblings,
       collect(DISTINCT contract.documentNumber) AS contract_ids,
       collect(DISTINCT po.documentNumber) AS po_numbers,
       collect(DISTINCT grn.documentNumber) AS grn_numbers,
       collect(DISTINCT pr.documentNumber) AS pr_numbers,
       collect(DISTINCT requester.partyName) AS requesters,
       collect(DISTINCT approver.partyName) AS approvers
