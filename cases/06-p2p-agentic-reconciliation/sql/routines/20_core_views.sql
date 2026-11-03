-- Ontology-facing views over versioned tables. The graph and RDF projections need one node per employee and an
-- approval that carries the limit that applied when it was given, so that history is not lost in the projection.

-- Grain: one employee, using the latest limit version.
create or replace view core.v_employee_current with (security_invoker = true) as
select distinct on (source_system, employee_id)
       source_system, employee_id, valid_from, valid_to, full_name, company_code, approval_limit, currency_code, is_deleted
from core.employee
order by source_system, employee_id, valid_from desc;

-- Grain: one approval step, plus the limit valid on the approval date.
create or replace view core.v_approval with (security_invoker = true) as
select a.source_system, a.pr_number, a.step, a.approver_id, a.approved_at, a.is_deleted,
       e.approval_limit as limit_at_approval, e.currency_code as limit_currency
from core.approval a
left join core.employee e on e.source_system = a.source_system and e.employee_id = a.approver_id
     and a.approved_at::date between e.valid_from and e.valid_to;

-- Grain: one bank account version, with the number masked. Deliberately NOT security_invoker: no user role can read the
-- number, so the view must read it as its owner, which is why it filters by company itself and is a security barrier.
create or replace view core.v_supplier_bank_account_masked with (security_barrier = true) as
select b.source_system, b.source_supplier_id, s.company_code, b.valid_from, b.is_deleted,
       repeat('*', greatest(length(b.account_number) - 4, 0)) || right(b.account_number, 4) as account_number_masked
from core.supplier_bank_account b
join core.supplier s on s.source_system = b.source_system and s.source_supplier_id = b.source_supplier_id
where s.company_code = any ((select agent.visible_companies())::text[]);
