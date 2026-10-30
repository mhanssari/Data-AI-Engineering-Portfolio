-- Deterministic matching engine. All money arithmetic and every release decision lives here (decision 0003).
--
-- recon.run_matching recomputes every invoice line on every run and stores a snapshot. Recomputing only "affected"
-- lines looks cheaper but is the riskier design: a corrected receipt changes cumulative quantities on sibling lines,
-- a new invoice can make an old one a duplicate's original, and a supplier merge changes duplicate detection across
-- the group. At this volume the full recompute is instant; comparing snapshots shows exactly what changed.

create or replace function core.policy_value(p_code text, p_as_of date) returns numeric
language sql stable as $$
    select parameter_value from core.policy_parameter
    where parameter_code = p_code and valid_from <= p_as_of and not is_deleted
    order by valid_from desc limit 1
$$;

create or replace function recon.normalise_invoice_number(p_number text) returns text
language sql immutable as $$ select upper(regexp_replace(p_number, '[^A-Za-z0-9]', '', 'g')) $$;

create or replace function recon.run_matching(p_run_id bigint, p_cutoff date) returns void
language plpgsql as $$
begin
    insert into recon.match_run (run_id, cutoff_date) values (p_run_id, p_cutoff);

    ------------------------------------------------------------------ Requisition controls
    create temp table _pr on commit drop as
    select pr.*, coalesce(x.supplier_gid, pr.source_supplier_id) as supplier_key,
           exists (select 1 from core.po_line l
                   where l.source_system = pr.source_system and l.pr_number = pr.pr_number and not l.is_deleted) as converted
    from core.purchase_requisition pr
    left join mdm.supplier_xref x on x.source_system = pr.source_system and x.source_supplier_id = pr.source_supplier_id
    where not pr.is_deleted;

    -- Requester approved their own requisition.
    insert into recon.control_breach (run_id, source_system, pr_number, company_code, reason_code, evidence)
    select p_run_id, pr.source_system, pr.pr_number, pr.company_code, 'SegregationOfDuties',
           jsonb_build_object('requester_id', pr.requester_id, 'approval_step', min(a.step))
    from _pr pr
    join core.approval a on a.source_system = pr.source_system and a.pr_number = pr.pr_number and not a.is_deleted
    where a.approver_id = pr.requester_id and pr.converted
    group by pr.source_system, pr.pr_number, pr.company_code, pr.requester_id;

    -- Converted without any approval.
    insert into recon.control_breach (run_id, source_system, pr_number, company_code, reason_code, evidence)
    select p_run_id, pr.source_system, pr.pr_number, pr.company_code, 'MissingApproval',
           jsonb_build_object('total_amount', pr.total_amount, 'currency_code', pr.currency_code)
    from _pr pr
    where pr.converted and not exists (
        select 1 from core.approval a
        where a.source_system = pr.source_system and a.pr_number = pr.pr_number and not a.is_deleted);

    -- At least one approver's limit, as valid on the approval date, must cover the amount in the limit's currency.
    -- Limit and rate are as of the approval, so a later limit increase cannot retrospectively clear a breach.
    -- A missing rate or a missing employee version leaves the limit uncovered: the control fails safe.
    insert into recon.control_breach (run_id, source_system, pr_number, company_code, reason_code, evidence)
    select p_run_id, s.source_system, s.pr_number, s.company_code, 'ApprovalLimitExceeded',
           jsonb_build_object('total_amount', s.total_amount, 'currency_code', s.currency_code,
                              'highest_limit_at_approval', max(s.approval_limit), 'approvers', jsonb_agg(s.approver_id order by s.step))
    from (
        select pr.source_system, pr.pr_number, pr.company_code, pr.total_amount, pr.currency_code, a.step, a.approver_id,
               e.approval_limit,
               case when e.currency_code = pr.currency_code then pr.total_amount
                    else round(pr.total_amount * fx.rate, 2) end as amount_in_limit_currency
        from _pr pr
        join core.approval a on a.source_system = pr.source_system and a.pr_number = pr.pr_number and not a.is_deleted
        left join core.employee e on e.source_system = a.source_system and e.employee_id = a.approver_id
             and a.approved_at::date between e.valid_from and e.valid_to and not e.is_deleted
        left join lateral (
            select r.rate from core.fx_rate r
            where r.from_currency = pr.currency_code and r.to_currency = e.currency_code
              and r.valid_from <= a.approved_at::date and not r.is_deleted
            order by r.valid_from desc limit 1
        ) fx on true
        where pr.converted
    ) s
    group by s.source_system, s.pr_number, s.company_code, s.total_amount, s.currency_code
    having not coalesce(bool_or(s.amount_in_limit_currency <= s.approval_limit), false);

    -- Split: from each anchor requisition, the requisitions by the same requester for the same resolved supplier in
    -- the window. A tier is breached when every one of them is within it but together they exceed it. Every member
    -- is flagged, because no single requisition in the group can be identified as the split.
    insert into recon.control_breach (run_id, source_system, pr_number, company_code, reason_code, evidence)
    select p_run_id, m.source_system, m.pr_number, m.company_code, 'SplitRequisition',
           jsonb_build_object('tier_breached', min(g.tier), 'group_total', max(g.group_total), 'currency_code', m.currency_code,
                              'window_days', core.policy_value('split_window_days', m.created_date))
    from _pr a
    join lateral (
        select t.parameter_value as tier, sum(b.total_amount) as group_total
        from _pr b
        join core.policy_parameter t on t.parameter_code like 'approval_tier_%' and not t.is_deleted
        where b.source_system = a.source_system and b.requester_id = a.requester_id
          and b.supplier_key = a.supplier_key and b.currency_code = a.currency_code
          and b.created_date between a.created_date
              and a.created_date + core.policy_value('split_window_days', a.created_date)::integer
        group by t.parameter_value
        having count(*) >= 2 and max(b.total_amount) <= t.parameter_value and sum(b.total_amount) > t.parameter_value
    ) g on true
    join _pr m on m.source_system = a.source_system and m.requester_id = a.requester_id
          and m.supplier_key = a.supplier_key and m.currency_code = a.currency_code
          and m.created_date between a.created_date
              and a.created_date + core.policy_value('split_window_days', a.created_date)::integer
    group by m.source_system, m.pr_number, m.company_code, m.currency_code, m.created_date;

    ------------------------------------------------------------------ Invoice-level facts
    create temp table _inv on commit drop as
    select i.*, coalesce(x.supplier_gid, i.source_system || ':' || i.source_supplier_id) as supplier_key,
           recon.normalise_invoice_number(i.supplier_invoice_number) as normalised_number
    from core.supplier_invoice i
    left join mdm.supplier_xref x on x.source_system = i.source_system and x.source_supplier_id = i.source_supplier_id
    where not i.is_deleted;

    -- Possible duplicate: the later of two documents with the same resolved supplier, normalised number, currency
    -- and amount inside the window. Matching on the golden supplier is what catches the same invoice sent to both ERPs.
    create temp table _duplicate on commit drop as
    select later.source_system, later.invoice_id,
           min(earlier.source_system || ':' || earlier.invoice_id) as original_invoice
    from _inv later
    join _inv earlier
      on earlier.supplier_key = later.supplier_key
     and earlier.normalised_number = later.normalised_number
     and earlier.currency_code = later.currency_code
     and earlier.net_amount = later.net_amount
     and earlier.document_type = 'INV'
     and (earlier.invoice_date, earlier.source_system, earlier.invoice_id)
         < (later.invoice_date, later.source_system, later.invoice_id)
     and later.invoice_date - earlier.invoice_date <= core.policy_value('duplicate_window_days', later.invoice_date)
    where later.document_type = 'INV'
    group by later.source_system, later.invoice_id;

    -- Bank change: a version starting within the window before the invoice. The first version on record is
    -- onboarding, not a change.
    create temp table _bank_change on commit drop as
    select i.source_system, i.invoice_id, max(b.valid_from) as changed_on
    from _inv i
    join core.supplier_bank_account b
      on b.source_system = i.source_system and b.source_supplier_id = i.source_supplier_id and not b.is_deleted
     and b.valid_from <= i.invoice_date
     and i.invoice_date - b.valid_from <= core.policy_value('bank_change_window_days', i.invoice_date)
     and exists (select 1 from core.supplier_bank_account e
                 where e.source_system = b.source_system and e.source_supplier_id = b.source_supplier_id
                   and e.valid_from < b.valid_from and not e.is_deleted)
    where i.document_type = 'INV'
    group by i.source_system, i.invoice_id;

    ------------------------------------------------------------------ Line facts
    create temp table _received on commit drop as
    select g.source_system, g.po_number, g.po_line_number,
           sum(g.received_quantity) as received_quantity, min(r.receipt_date) as first_receipt_date
    from core.grn_line g
    join core.goods_receipt r on r.source_system = g.source_system and r.grn_number = g.grn_number
    where not g.is_deleted and not r.is_deleted
    group by g.source_system, g.po_number, g.po_line_number;

    create temp table _line on commit drop as
    select l.source_system, l.invoice_id, l.line_number, l.po_number, l.po_line_number,
           l.invoiced_quantity, l.net_amount,
           i.company_code, i.currency_code, i.invoice_date, i.document_type, i.credited_invoice_id, i.supplier_key,
           d.original_invoice as duplicate_of, b.changed_on as bank_changed_on,
           pl.match_type as po_match_type, pl.unit_price as po_unit_price, pl.quantity as po_quantity,
           pl.pr_number, pl.contract_id, po.po_date, po.currency_code as po_currency_code,
           coalesce(r.received_quantity, 0) as received_quantity, r.first_receipt_date,
           -- Held duplicates do not consume quantity: otherwise a later genuine invoice would inherit their overrun.
           case when d.invoice_id is null then
               sum(l.invoiced_quantity) filter (where d.invoice_id is null) over (
                   partition by l.source_system, l.po_number, l.po_line_number
                   order by i.invoice_date, l.invoice_id, l.line_number)
           end as cumulative_invoiced
    from core.invoice_line l
    join _inv i on i.source_system = l.source_system and i.invoice_id = l.invoice_id
    left join _duplicate d on d.source_system = l.source_system and d.invoice_id = l.invoice_id
    left join _bank_change b on b.source_system = l.source_system and b.invoice_id = l.invoice_id
    left join core.po_line pl on pl.source_system = l.source_system and pl.po_number = l.po_number
         and pl.line_number = l.po_line_number and not pl.is_deleted
    left join core.purchase_order po on po.source_system = l.source_system and po.po_number = l.po_number and not po.is_deleted
    left join _received r on r.source_system = l.source_system and r.po_number = l.po_number and r.po_line_number = l.po_line_number
    where not l.is_deleted;

    -- Tolerance is the contract's if a version is in force at the invoice date, otherwise the policy default.
    create temp table _assessed on commit drop as
    select l.*,
           round(l.invoiced_quantity * l.po_unit_price, 2) as expected_amount,
           l.net_amount - round(l.invoiced_quantity * l.po_unit_price, 2) as amount_variance,
           coalesce(c.price_tolerance_pct, core.policy_value('price_tolerance_pct_default', l.invoice_date)) as tolerance_pct,
           case when c.contract_id is not null then 'contract:' || c.contract_id else 'policy' end as tolerance_source,
           core.policy_value('rounding_tolerance_amount', l.invoice_date) as rounding_tolerance,
           case when l.po_match_type = 'TWO_WAY' then l.po_quantity else l.received_quantity end as allowed_quantity
    from _line l
    left join core.contract c on c.contract_id = l.contract_id
         and l.invoice_date between c.valid_from and c.valid_to and not c.is_deleted;

    ------------------------------------------------------------------ Exceptions
    create temp table _exc (source_system text, invoice_id text, line_number integer, reason_code text, evidence jsonb) on commit drop;

    insert into _exc
    select source_system, invoice_id, line_number, 'NoPurchaseOrder', '{}'::jsonb
    from _assessed where po_number is null and document_type = 'INV';

    insert into _exc
    select source_system, invoice_id, line_number, 'CurrencyMismatch',
           jsonb_build_object('invoice_currency', currency_code, 'po_currency', po_currency_code)
    from _assessed where po_number is not null and currency_code <> po_currency_code and document_type = 'INV';

    -- Amounts are not comparable across currencies, so price is only assessed when the currencies agree.
    -- Invoicing below the PO price is not a breach: the policy limits overcharging only.
    insert into _exc
    select source_system, invoice_id, line_number, 'PriceVariance',
           jsonb_build_object('expected_amount', expected_amount, 'net_amount', net_amount,
                              'variance_pct', round(amount_variance / nullif(expected_amount, 0) * 100, 4),
                              'tolerance_pct', tolerance_pct, 'tolerance_source', tolerance_source)
    from _assessed
    where document_type = 'INV' and po_number is not null and currency_code = po_currency_code
      and amount_variance > rounding_tolerance
      and amount_variance / nullif(expected_amount, 0) * 100 > tolerance_pct;

    -- Goods with no receipt at all are PendingReceipt, not a variance: there is nothing yet to disagree with.
    insert into _exc
    select source_system, invoice_id, line_number, 'QuantityVariance',
           jsonb_build_object('cumulative_invoiced', cumulative_invoiced, 'allowed_quantity', allowed_quantity,
                              'basis', case po_match_type when 'TWO_WAY' then 'ordered' else 'received' end)
    from _assessed
    where document_type = 'INV' and po_number is not null and duplicate_of is null
      and (po_match_type = 'TWO_WAY' or first_receipt_date is not null)
      and cumulative_invoiced > allowed_quantity * (1 + core.policy_value('quantity_tolerance_pct', invoice_date) / 100);

    insert into _exc
    select source_system, invoice_id, line_number, 'InvoiceBeforeReceipt',
           jsonb_build_object('invoice_date', invoice_date, 'first_receipt_date', first_receipt_date)
    from _assessed
    where document_type = 'INV' and po_match_type = 'THREE_WAY' and invoice_date < first_receipt_date;

    insert into _exc
    select source_system, invoice_id, line_number, 'RetrospectivePO',
           jsonb_build_object('invoice_date', invoice_date, 'po_date', po_date)
    from _assessed where document_type = 'INV' and po_date > invoice_date;

    insert into _exc
    select source_system, invoice_id, line_number, 'MissingRequisition', jsonb_build_object('po_number', po_number)
    from _assessed where document_type = 'INV' and po_number is not null and pr_number is null;

    insert into _exc
    select source_system, invoice_id, line_number, 'DuplicateInvoice', jsonb_build_object('original_invoice', duplicate_of)
    from _assessed where duplicate_of is not null;

    insert into _exc
    select source_system, invoice_id, line_number, 'BankDetailChange', jsonb_build_object('changed_on', bank_changed_on)
    from _assessed where bank_changed_on is not null;

    -- A control breach on the requisition holds every invoice line it funds; the breach evidence stays on the requisition.
    insert into _exc
    select a.source_system, a.invoice_id, a.line_number, b.reason_code,
           jsonb_build_object('pr_number', b.pr_number)
    from _assessed a
    join recon.control_breach b on b.run_id = p_run_id and b.source_system = a.source_system and b.pr_number = a.pr_number
    where a.document_type = 'INV';

    -- A credit note must reference an invoice of the same resolved supplier and, cumulatively, not exceed it.
    insert into _exc
    select c.source_system, c.invoice_id, a.line_number, 'UnmatchedCreditNote',
           jsonb_build_object('credited_invoice_id', c.credited_invoice_id, 'original_net_amount', o.net_amount,
                              'cumulative_credited', c.cumulative_credited)
    from (
        select n.*, sum(-n.net_amount) over (partition by n.source_system, n.credited_invoice_id
                                             order by n.invoice_date, n.invoice_id) as cumulative_credited
        from _inv n where n.document_type = 'CRN'
    ) c
    left join _inv o on o.source_system = c.source_system and o.invoice_id = c.credited_invoice_id and o.document_type = 'INV'
    join _assessed a on a.source_system = c.source_system and a.invoice_id = c.invoice_id
    where o.invoice_id is null or o.supplier_key <> c.supplier_key or c.cumulative_credited > o.net_amount;

    ------------------------------------------------------------------ Results
    insert into recon.match_result (run_id, source_system, invoice_id, line_number, company_code, currency_code, document_type,
                                    match_type, status_code, net_amount, expected_amount, amount_variance, tolerance_pct,
                                    tolerance_source, cumulative_invoiced, allowed_quantity)
    select p_run_id, a.source_system, a.invoice_id, a.line_number, a.company_code, a.currency_code, a.document_type,
           case when a.document_type = 'CRN' then 'CREDIT' when a.po_number is null then 'NONE' else a.po_match_type end,
           case when exists (select 1 from _exc e where e.source_system = a.source_system and e.invoice_id = a.invoice_id
                             and e.line_number = a.line_number) then 'Exception'
                when a.document_type = 'CRN' then 'Matched'
                when a.po_match_type = 'THREE_WAY' and a.first_receipt_date is null then 'PendingReceipt'
                when abs(a.amount_variance) <= a.rounding_tolerance then 'Matched'
                else 'MatchedWithinTolerance' end,
           a.net_amount, a.expected_amount, a.amount_variance, a.tolerance_pct, a.tolerance_source,
           a.cumulative_invoiced, a.allowed_quantity
    from _assessed a;

    insert into recon.exception (run_id, source_system, invoice_id, line_number, reason_code, evidence)
    select p_run_id, source_system, invoice_id, line_number, reason_code, evidence from _exc;

    ------------------------------------------------------------------ GRNI and the proposed accrual
    -- As of the cut-off, on document dates. Held duplicates and rejected credit notes do not reduce the accrual;
    -- a valid credit note does, until a return receipt is posted (returns are not modelled in this milestone).
    insert into recon.grni_line (run_id, cutoff_date, source_system, po_number, line_number, company_code, currency_code,
                                 gl_account_code, received_quantity, invoiced_quantity, grni_quantity, unit_price, grni_amount)
    select p_run_id, p_cutoff, q.source_system, q.po_number, q.line_number, q.company_code, q.currency_code, q.gl_account_code,
           q.received, q.invoiced, q.received - q.invoiced, q.unit_price, round((q.received - q.invoiced) * q.unit_price, 2)
    from (
        select pl.source_system, pl.po_number, pl.line_number, po.company_code, po.currency_code, pl.gl_account_code, pl.unit_price,
               rec.received, coalesce(inv.invoiced, 0) as invoiced
        from core.po_line pl
        join core.purchase_order po on po.source_system = pl.source_system and po.po_number = pl.po_number and not po.is_deleted
        join lateral (
            select sum(g.received_quantity) as received
            from core.grn_line g
            join core.goods_receipt r on r.source_system = g.source_system and r.grn_number = g.grn_number
            where g.source_system = pl.source_system and g.po_number = pl.po_number and g.po_line_number = pl.line_number
              and r.receipt_date <= p_cutoff and not g.is_deleted and not r.is_deleted
        ) rec on rec.received is not null
        left join lateral (
            select sum(l.invoiced_quantity) as invoiced
            from core.invoice_line l
            join _inv i on i.source_system = l.source_system and i.invoice_id = l.invoice_id
            where l.source_system = pl.source_system and l.po_number = pl.po_number and l.po_line_number = pl.line_number
              and i.invoice_date <= p_cutoff and not l.is_deleted
              and not exists (select 1 from _duplicate d where d.source_system = i.source_system and d.invoice_id = i.invoice_id)
              and not exists (select 1 from _exc e where e.source_system = l.source_system and e.invoice_id = l.invoice_id
                              and e.reason_code = 'UnmatchedCreditNote')
        ) inv on true
        where pl.match_type = 'THREE_WAY' and not pl.is_deleted
    ) q
    where q.received > q.invoiced;

    insert into recon.accrual_journal_line (run_id, cutoff_date, company_code, currency_code, gl_account_code, side, amount)
    select p_run_id, p_cutoff, company_code, currency_code, gl_account_code, 'D', sum(grni_amount)
    from recon.grni_line where run_id = p_run_id
    group by company_code, currency_code, gl_account_code
    union all
    select p_run_id, p_cutoff, g.company_code, g.currency_code, le.grni_account, 'C', sum(g.grni_amount)
    from recon.grni_line g join core.legal_entity le on le.company_code = g.company_code
    where g.run_id = p_run_id
    group by g.company_code, g.currency_code, le.grni_account;
end;
$$;

------------------------------------------------------------------ Current-state views
create or replace view recon.latest_run as
select run_id, cutoff_date from recon.match_run order by run_id desc limit 1;

create or replace view recon.current_match_result as
select m.* from recon.match_result m join recon.latest_run r using (run_id);

create or replace view recon.current_exception as
select e.* from recon.exception e join recon.latest_run r using (run_id);

create or replace view recon.current_control_breach as
select b.* from recon.control_breach b join recon.latest_run r using (run_id);

create or replace view recon.current_grni_line as
select g.* from recon.grni_line g join recon.latest_run r using (run_id);

create or replace view recon.current_accrual_journal_line as
select j.* from recon.accrual_journal_line j join recon.latest_run r using (run_id);

-- Grain: one invoice. Payment readiness is a release proposal for a person to approve, never a payment.
create or replace view recon.current_payment_readiness as
select m.source_system, m.invoice_id, m.company_code, m.currency_code, sum(m.net_amount) as net_amount,
       case when bool_and(m.status_code in ('Matched', 'MatchedWithinTolerance')) then 'ReadyForApproval' else 'Held' end as readiness,
       count(*) filter (where m.status_code not in ('Matched', 'MatchedWithinTolerance')) as lines_blocking
from recon.current_match_result m
group by m.source_system, m.invoice_id, m.company_code, m.currency_code;
