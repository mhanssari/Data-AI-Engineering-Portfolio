-- Staging views: one per canonical entity, a union of both source shapes in the canonical column names.
-- Grain: one delivered row version, so a key can appear many times; conform takes the latest per key.
-- Parsing lives here and only here. A malformed value fails the conform run rather than being guessed at.
-- Repeatable: views hold no data, so the whole schema is rebuilt on every migrate.

drop schema if exists stg cascade;
create schema stg;

create function stg.sap_date(text) returns date language sql immutable as
    $$ select to_date($1, 'YYYYMMDD') $$;
create function stg.sap_ts(text) returns timestamptz language sql stable as
    $$ select to_timestamp($1, 'YYYYMMDDHH24MISS')::timestamp at time zone 'UTC' $$;
create function stg.ora_date(text) returns date language sql immutable as
    $$ select to_date($1, 'DD-MON-YYYY') $$;
create function stg.ora_ts(text) returns timestamptz language sql stable as
    $$ select $1::timestamp at time zone 'UTC' $$;

-- Latest org-reference mapping: Oracle identifies companies by org_id, the canonical model by company code.
create view stg.org_map as
select distinct on (source_org_ref) source_org_ref, company_code
from raw.group_legal_entities
order by source_org_ref, changed_at desc, raw_id desc;

create view stg.legal_entity as
select company_code, name as entity_name, erp_system as source_system, source_org_ref, currency_code, country_code, grni_account,
       raw_id, stg.ora_ts(changed_at) as source_changed_at, cdc_op
from raw.group_legal_entities;

create view stg.policy_parameter as
select parameter_code, policy_version, value::numeric(18,4) as parameter_value, unit, valid_from::date as valid_from,
       'GROUP' as source_system, raw_id, stg.ora_ts(changed_at) as source_changed_at, cdc_op
from raw.group_policy_parameters;

create view stg.fx_rate as
select from_currency, to_currency, valid_from::date as valid_from, rate::numeric(18,6) as rate,
       'GROUP' as source_system, raw_id, stg.ora_ts(changed_at) as source_changed_at, cdc_op
from raw.group_fx_rates;

create view stg.item as
select item_id, description, item_type, uom,
       'GROUP' as source_system, raw_id, stg.ora_ts(changed_at) as source_changed_at, cdc_op
from raw.group_items;

create view stg.cost_centre as
select bukrs as company_code, kostl as cost_centre_code, ktext as description, 'SAP_LIKE' as source_system,
       raw_id, stg.sap_ts(changed_at) as source_changed_at, cdc_op
from raw.sap_cost_centre
union all
select m.company_code, cost_centre, description, 'ORACLE_LIKE',
       r.raw_id, stg.ora_ts(last_update_date), cdc_op
from raw.ora_cost_centres r left join stg.org_map m on m.source_org_ref = r.org_id;

create view stg.gl_account as
select bukrs as company_code, saknr as gl_account_code, txt50 as description, 'SAP_LIKE' as source_system,
       raw_id, stg.sap_ts(changed_at) as source_changed_at, cdc_op
from raw.sap_gl_account
union all
select m.company_code, account, description, 'ORACLE_LIKE',
       r.raw_id, stg.ora_ts(last_update_date), cdc_op
from raw.ora_gl_accounts r left join stg.org_map m on m.source_org_ref = r.org_id;

-- A supplier version takes the latest primary pay site, and is as new as whichever of the two changed last,
-- so a site-only change still reaches core.
create view stg.supplier as
select 'SAP_LIKE' as source_system, lifnr as source_supplier_id, bukrs as company_code, name1 as supplier_name,
       stras as address_line, ort01 as city, pstlz as postcode, land1 as country_code, stceg as vat_number,
       raw_id, stg.sap_ts(changed_at) as source_changed_at, cdc_op
from raw.sap_supplier
union all
select 'ORACLE_LIKE', r.vendor_id, m.company_code, r.vendor_name,
       site.address_line1, site.city, site.zip, site.country, r.vat_registration_num,
       r.raw_id, greatest(stg.ora_ts(r.last_update_date), stg.ora_ts(site.last_update_date)), r.cdc_op
from raw.ora_suppliers r
left join stg.org_map m on m.source_org_ref = r.org_id
left join lateral (
    select s.* from raw.ora_supplier_sites s
    where s.vendor_id = r.vendor_id and s.primary_pay_site_flag = 'Y'
    order by s.last_update_date desc, s.raw_id desc limit 1
) site on true;

create view stg.supplier_bank_account as
select 'SAP_LIKE' as source_system, lifnr as source_supplier_id, stg.sap_date(valid_from) as valid_from,
       iban as account_number, raw_id, stg.sap_ts(changed_at) as source_changed_at, cdc_op
from raw.sap_supplier_bank
union all
select 'ORACLE_LIKE', vendor_id, stg.ora_date(start_date), iban,
       raw_id, stg.ora_ts(last_update_date), cdc_op
from raw.ora_supplier_bank_accounts;

create view stg.employee as
select 'SAP_LIKE' as source_system, pernr as employee_id, stg.sap_date(valid_from) as valid_from,
       stg.sap_date(valid_to) as valid_to, ename as full_name, bukrs as company_code,
       appr_limit::numeric(18,2) as approval_limit, waers as currency_code,
       raw_id, stg.sap_ts(changed_at) as source_changed_at, cdc_op
from raw.sap_employee
union all
select 'ORACLE_LIKE', person_id, stg.ora_date(effective_start_date), stg.ora_date(effective_end_date), full_name,
       m.company_code, approval_limit::numeric(18,2), currency_code,
       r.raw_id, stg.ora_ts(last_update_date), cdc_op
from raw.ora_employees r left join stg.org_map m on m.source_org_ref = r.org_id;

create view stg.contract as
select contract_id, valid_from::date as valid_from, valid_to::date as valid_to, company_code, erp_system as source_system,
       source_supplier_id, item_id, agreed_price::numeric(18,6) as agreed_price, currency_code,
       price_tolerance_pct::numeric(7,4) as price_tolerance_pct,
       raw_id, stg.ora_ts(changed_at) as source_changed_at, cdc_op
from raw.group_contracts;

create view stg.purchase_requisition as
select 'SAP_LIKE' as source_system, banfn as pr_number, bukrs as company_code, afnam as requester_id,
       lifnr as source_supplier_id, matnr as item_id, menge::numeric(18,3) as quantity,
       preis::numeric(18,6) as unit_price, round(menge::numeric(18,3) * preis::numeric(18,6), 2) as total_amount,
       waers as currency_code, stg.sap_date(badat) as created_date, kostl as cost_centre_code, saknr as gl_account_code,
       raw_id, stg.sap_ts(changed_at) as source_changed_at, cdc_op
from raw.sap_pr
union all
select 'ORACLE_LIKE', requisition_number, m.company_code, preparer_id, suggested_vendor_id, item_code,
       quantity::numeric(18,3), unit_price::numeric(18,6), round(quantity::numeric(18,3) * unit_price::numeric(18,6), 2),
       currency_code, stg.ora_date(creation_date), cost_centre, account,
       r.raw_id, stg.ora_ts(last_update_date), cdc_op
from raw.ora_requisitions r left join stg.org_map m on m.source_org_ref = r.org_id;

create view stg.approval as
select 'SAP_LIKE' as source_system, banfn as pr_number, step::integer as step, approver as approver_id,
       stg.sap_ts(approved_at) as approved_at, raw_id, stg.sap_ts(changed_at) as source_changed_at, cdc_op
from raw.sap_pr_approval
union all
select 'ORACLE_LIKE', requisition_number, sequence_num::integer, approver_id, stg.ora_ts(action_date),
       raw_id, stg.ora_ts(last_update_date), cdc_op
from raw.ora_approval_history
where action_code = 'APPROVE';

create view stg.purchase_order as
select 'SAP_LIKE' as source_system, ebeln as po_number, bukrs as company_code, lifnr as source_supplier_id,
       stg.sap_date(bedat) as po_date, waers as currency_code,
       raw_id, stg.sap_ts(changed_at) as source_changed_at, cdc_op
from raw.sap_po_header
union all
select 'ORACLE_LIKE', po_number, m.company_code, vendor_id, stg.ora_date(creation_date), currency_code,
       r.raw_id, stg.ora_ts(last_update_date), cdc_op
from raw.ora_po_headers r left join stg.org_map m on m.source_org_ref = r.org_id;

-- SAP numbers lines 00010, 00020; Oracle 1, 2. Both become integers and stay as delivered otherwise.
create view stg.po_line as
select 'SAP_LIKE' as source_system, i.ebeln as po_number, i.ebelp::integer as line_number, h.bukrs as company_code,
       i.banfn as pr_number, i.matnr as item_id, i.menge::numeric(18,3) as quantity, i.meins as uom,
       i.netpr::numeric(18,6) as unit_price, i.kostl as cost_centre_code, i.saknr as gl_account_code,
       i.konnr as contract_id, case when i.wepos = 'X' then 'THREE_WAY' else 'TWO_WAY' end as match_type,
       i.raw_id, stg.sap_ts(i.changed_at) as source_changed_at, i.cdc_op
from raw.sap_po_item i
left join lateral (
    select bukrs from raw.sap_po_header x where x.ebeln = i.ebeln order by x.changed_at desc, x.raw_id desc limit 1
) h on true
union all
select 'ORACLE_LIKE', i.po_number, i.line_num::integer, h.company_code,
       i.requisition_number, i.item_code, i.quantity::numeric(18,3), i.uom_code,
       i.unit_price::numeric(18,6), i.cost_centre, i.account,
       i.contract_num, case when i.match_option = 'R' then 'THREE_WAY' else 'TWO_WAY' end,
       i.raw_id, stg.ora_ts(i.last_update_date), i.cdc_op
from raw.ora_po_lines i
left join lateral (
    select m.company_code from raw.ora_po_headers x
    left join stg.org_map m on m.source_org_ref = x.org_id
    where x.po_number = i.po_number order by x.last_update_date desc, x.raw_id desc limit 1
) h on true;

-- Both sources deliver receipts at line level; this view derives the header. Conform keeps the latest row per document.
create view stg.goods_receipt as
select 'SAP_LIKE' as source_system, mblnr as grn_number, bukrs as company_code, stg.sap_date(budat) as receipt_date,
       raw_id, stg.sap_ts(changed_at) as source_changed_at, cdc_op
from raw.sap_goods_receipt
union all
select 'ORACLE_LIKE', receipt_num, m.company_code, stg.ora_date(transaction_date),
       r.raw_id, stg.ora_ts(last_update_date), cdc_op
from raw.ora_receipts r left join stg.org_map m on m.source_org_ref = r.org_id;

create view stg.grn_line as
select 'SAP_LIKE' as source_system, mblnr as grn_number, zeile::integer as line_number, ebeln as po_number,
       ebelp::integer as po_line_number, menge::numeric(18,3) as received_quantity,
       raw_id, stg.sap_ts(changed_at) as source_changed_at, cdc_op
from raw.sap_goods_receipt
union all
select 'ORACLE_LIKE', receipt_num, line_num::integer, po_number, po_line_num::integer, quantity_received::numeric(18,3),
       raw_id, stg.ora_ts(last_update_date), cdc_op
from raw.ora_receipts;

-- Sign conventions differ: SAP delivers positive credit memos marked blart = 'KG', Oracle delivers negative amounts.
create view stg.supplier_invoice as
select 'SAP_LIKE' as source_system, belnr as invoice_id, bukrs as company_code, lifnr as source_supplier_id,
       xblnr as supplier_invoice_number, stg.sap_date(bldat) as invoice_date, waers as currency_code,
       case when blart = 'KG' then -1 else 1 end * netwr::numeric(18,2) as net_amount,
       case when blart = 'KG' then 'CRN' else 'INV' end as document_type, rebzg as credited_invoice_id,
       raw_id, stg.sap_ts(changed_at) as source_changed_at, cdc_op
from raw.sap_invoice_header
union all
select 'ORACLE_LIKE', invoice_id, m.company_code, vendor_id, invoice_num, stg.ora_date(invoice_date),
       invoice_currency_code, invoice_amount::numeric(18,2),
       case when invoice_type_lookup_code = 'CREDIT' then 'CRN' else 'INV' end, credited_invoice_id,
       r.raw_id, stg.ora_ts(last_update_date), cdc_op
from raw.ora_invoices r left join stg.org_map m on m.source_org_ref = r.org_id;

-- SAP lines carry no unit price and take their sign from the header; the price is derived and stays positive.
create view stg.invoice_line as
select 'SAP_LIKE' as source_system, i.belnr as invoice_id, i.buzei::integer as line_number, i.ebeln as po_number,
       i.ebelp::integer as po_line_number,
       case when h.blart = 'KG' then -1 else 1 end * i.menge::numeric(18,3) as invoiced_quantity,
       round(i.wrbtr::numeric(18,2) / nullif(i.menge::numeric(18,3), 0), 6) as unit_price,
       case when h.blart = 'KG' then -1 else 1 end * i.wrbtr::numeric(18,2) as net_amount,
       i.raw_id, stg.sap_ts(i.changed_at) as source_changed_at, i.cdc_op
from raw.sap_invoice_item i
left join lateral (
    select blart from raw.sap_invoice_header x where x.belnr = i.belnr order by x.changed_at desc, x.raw_id desc limit 1
) h on true
union all
select 'ORACLE_LIKE', invoice_id, line_number::integer, po_number, po_line_num::integer, quantity_invoiced::numeric(18,3),
       unit_price::numeric(18,6), amount::numeric(18,2), raw_id, stg.ora_ts(last_update_date), cdc_op
from raw.ora_invoice_lines;
