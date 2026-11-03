// All invoices for one golden supplier, across every source identifier. Parameter: $supplier_gid.
MATCH (golden:GoldenSupplier {goldenId: $supplier_gid})<-[:RESOLVES_TO]-(source:Supplier)<-[:ISSUED_BY]-(inv:SupplierInvoice)
RETURN source.sourceSystem AS source_system,
       source.sourceKey AS source_supplier_id,
       inv.sourceKey AS invoice_id,
       inv.documentNumber AS invoice_number,
       inv.documentDate AS invoice_date,
       inv.amount AS net_amount,
       inv.currencyCode AS currency
ORDER BY invoice_date, source_system, invoice_id
