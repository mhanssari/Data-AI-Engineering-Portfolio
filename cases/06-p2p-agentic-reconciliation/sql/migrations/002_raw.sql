-- Raw landing tables: one per source file type, every source column as text exactly as delivered.
-- Grain: one row of one delivered file. Append-only; nothing in raw is ever updated or deleted.
-- Metadata columns: raw_id (row identity for lineage), run_id, source_system, batch_id, source_file,
-- file_row, extracted_at (from the batch manifest) and cdc_op (I/U/D in change-log files, null in full
-- extracts). Column names follow each source's own conventions on purpose; stg maps them to the canonical
-- model. CREATE TABLE ... LIKE does not copy foreign keys, so run_id is not enforced here: the ingest step
-- is the only writer and always takes it from ops.pipeline_run.

-- One sequence for all raw tables, so raw_id identifies a landed row across the whole schema.
create sequence raw.raw_id_seq;

create table raw.meta_template (
    raw_id        bigint primary key default nextval('raw.raw_id_seq'),
    run_id        bigint not null,
    source_system text not null,
    batch_id      text not null,
    source_file   text not null,
    file_row      integer not null,
    extracted_at  timestamptz not null,
    cdc_op        text
);

---------------------------------------------------------------- SAP-like (UK company, semicolon files)
create table raw.sap_supplier       (like raw.meta_template including all,
    lifnr text, name1 text, stras text, ort01 text, pstlz text, land1 text, stceg text, bukrs text, changed_at text);
create table raw.sap_supplier_bank  (like raw.meta_template including all,
    lifnr text, iban text, valid_from text, changed_at text);
create table raw.sap_employee       (like raw.meta_template including all,
    pernr text, ename text, bukrs text, appr_limit text, waers text, valid_from text, valid_to text, changed_at text);
create table raw.sap_cost_centre    (like raw.meta_template including all,
    kostl text, bukrs text, ktext text, changed_at text);
create table raw.sap_gl_account     (like raw.meta_template including all,
    saknr text, bukrs text, txt50 text, changed_at text);
create table raw.sap_pr             (like raw.meta_template including all,
    banfn text, bukrs text, afnam text, lifnr text, matnr text, menge text, preis text, waers text,
    badat text, kostl text, saknr text, changed_at text);
create table raw.sap_pr_approval    (like raw.meta_template including all,
    banfn text, step text, approver text, approved_at text, changed_at text);
create table raw.sap_po_header      (like raw.meta_template including all,
    ebeln text, bukrs text, lifnr text, bedat text, waers text, changed_at text);
create table raw.sap_po_item        (like raw.meta_template including all,
    ebeln text, ebelp text, banfn text, matnr text, menge text, meins text, netpr text, kostl text,
    saknr text, konnr text, wepos text, changed_at text);
create table raw.sap_goods_receipt  (like raw.meta_template including all,
    mblnr text, zeile text, bukrs text, budat text, ebeln text, ebelp text, menge text, changed_at text);
create table raw.sap_invoice_header (like raw.meta_template including all,
    belnr text, bukrs text, lifnr text, xblnr text, bldat text, waers text, netwr text, blart text,
    rebzg text, changed_at text);
create table raw.sap_invoice_item   (like raw.meta_template including all,
    belnr text, buzei text, ebeln text, ebelp text, menge text, wrbtr text, changed_at text);

---------------------------------------------------------------- Oracle-like (German company, comma files)
create table raw.ora_suppliers              (like raw.meta_template including all,
    vendor_id text, vendor_name text, vat_registration_num text, org_id text, last_update_date text);
create table raw.ora_supplier_sites         (like raw.meta_template including all,
    vendor_site_id text, vendor_id text, address_line1 text, city text, zip text, country text,
    primary_pay_site_flag text, last_update_date text);
create table raw.ora_supplier_bank_accounts (like raw.meta_template including all,
    vendor_id text, iban text, start_date text, last_update_date text);
create table raw.ora_employees              (like raw.meta_template including all,
    person_id text, full_name text, org_id text, approval_limit text, currency_code text,
    effective_start_date text, effective_end_date text, last_update_date text);
create table raw.ora_cost_centres           (like raw.meta_template including all,
    cost_centre text, org_id text, description text, last_update_date text);
create table raw.ora_gl_accounts            (like raw.meta_template including all,
    account text, org_id text, description text, last_update_date text);
create table raw.ora_requisitions           (like raw.meta_template including all,
    requisition_number text, org_id text, preparer_id text, suggested_vendor_id text, item_code text,
    quantity text, unit_price text, currency_code text, creation_date text, cost_centre text, account text,
    last_update_date text);
create table raw.ora_approval_history       (like raw.meta_template including all,
    requisition_number text, sequence_num text, approver_id text, action_code text, action_date text,
    last_update_date text);
create table raw.ora_po_headers             (like raw.meta_template including all,
    po_number text, org_id text, vendor_id text, creation_date text, currency_code text, last_update_date text);
create table raw.ora_po_lines               (like raw.meta_template including all,
    po_number text, line_num text, requisition_number text, item_code text, quantity text, uom_code text,
    unit_price text, cost_centre text, account text, contract_num text, match_option text, last_update_date text);
create table raw.ora_receipts               (like raw.meta_template including all,
    receipt_num text, line_num text, org_id text, transaction_date text, po_number text, po_line_num text,
    quantity_received text, last_update_date text);
create table raw.ora_invoices               (like raw.meta_template including all,
    invoice_id text, invoice_num text, vendor_id text, org_id text, invoice_date text,
    invoice_currency_code text, invoice_amount text, invoice_type_lookup_code text, credited_invoice_id text,
    last_update_date text);
create table raw.ora_invoice_lines          (like raw.meta_template including all,
    invoice_id text, line_number text, po_number text, po_line_num text, quantity_invoiced text,
    unit_price text, amount text, last_update_date text);

---------------------------------------------------------------- Group reference data (comma files)
create table raw.group_legal_entities       (like raw.meta_template including all,
    company_code text, name text, erp_system text, source_org_ref text, currency_code text,
    country_code text, grni_account text, changed_at text);
create table raw.group_items                (like raw.meta_template including all,
    item_id text, description text, item_type text, uom text, changed_at text);
create table raw.group_contracts            (like raw.meta_template including all,
    contract_id text, valid_from text, valid_to text, company_code text, erp_system text,
    source_supplier_id text, item_id text, agreed_price text, currency_code text, price_tolerance_pct text,
    changed_at text);
create table raw.group_policy_parameters    (like raw.meta_template including all,
    parameter_code text, value text, unit text, policy_version text, valid_from text, changed_at text);
-- Needed because a company can raise spend in a foreign currency while approval limits are in company currency.
create table raw.group_fx_rates             (like raw.meta_template including all,
    from_currency text, to_currency text, rate text, valid_from text, changed_at text);

drop table raw.meta_template;
