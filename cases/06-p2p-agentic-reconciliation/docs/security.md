# Security and controls

Status: built and tested locally on synthetic data. Sign-in is simulated, so this demonstrates how the database enforces access, not authentication. See [decision 0009](decisions/0009-security-model.md), [`sql/migrations/006_security.sql`](../sql/migrations/006_security.sql), [`007_admin_review.sql`](../sql/migrations/007_admin_review.sql), [`tests/test_security.py`](../tests/test_security.py) and [`tests/test_admin_security.py`](../tests/test_admin_security.py).

## Roles

| Role | Sees | Can do |
| --- | --- | --- |
| `ap_analyst_uk` | GB01 rows and documents | Decide ordinary exceptions in GB01 |
| `ap_analyst_de` | DE01 rows and documents | Decide ordinary exceptions in DE01 |
| `controller` | Both companies; steward queue; audit log | Decide anything, including failed controls |
| `auditor` | Both companies; audit memos; steward queue; audit log | Read only |
| `admin_reviewer` | Both companies, all documents including audit memos, the agent trail, `mdm`, `ops` and the audit log. Masked bank numbers only; no `raw` | Write review notes. Nothing else: no decisions, no business data |
| `agent_service` | No business data of its own | Write the agent trail, and act for a person |
| `p2p_app` (login) | Nothing until it assumes a role | Assume any role above. Not the superuser |

Failed controls are approval limit, segregation of duties, missing approval, split requisition, retrospective PO and missing requisition.

## Enforcement

| Rule | Where it is enforced | Proof |
| --- | --- | --- |
| An analyst sees one company | Row-level security on `core`, `recon`, `docs`, the agent trail and the supplier crosswalk. Child tables follow their parent | Allowed and denied tests on every table |
| Views cannot bypass it | `security_invoker` on every view; a catalogue test finds any that is not | `test_every_view_runs_as_its_reader` |
| A new company table cannot be forgotten | A catalogue test finds any table with `company_code` and no row security | `test_every_table_with_a_company_has_row_security` |
| Agents have the person's limits | Tools run under `SET LOCAL ROLE` as the acting user, reverting to `agent_service` for logging | Tests with the query's own filter removed |
| Nobody reads a bank number | Column revoked from every role, including the controller and auditor; `core.v_supplier_bank_account_masked` shows the last four characters | `test_nobody_reads_a_bank_number` |
| An analyst cannot clear a failed control | Policy on `agent.human_decision`, so no interface can skip it | `test_analyst_may_not_clear_a_failed_control` |
| Nobody decides in another's name | The same policy requires `decided_by = current_user` | `test_nobody_can_decide_in_someone_elses_name` |
| The trail cannot be rewritten | Triggers refuse update, delete and truncate on proposals, citations, decisions and the audit log, even for the owner | `test_the_trail_cannot_be_rewritten_even_by_the_owner` |
| An action cannot go unlogged | `agent.audit_log` is written by triggers, and no role can insert into it directly | `test_actions_reach_the_audit_log_with_who_acted_for_whom` |
| The reviewer sees everything it should | It is listed in `agent.reviewer`, which `agent.visible_companies()` and `visible_tags()` read, so every existing policy applies to it unchanged. A test reads the protected tables from the catalogue and compares counts | `test_reviewer_sees_every_row_of_every_protected_table` |
| The reviewer decides nothing and is never acted for | It is not in `agent.acting_user`, which `agent.run.acting_user` and `agent.human_decision.decided_by` reference, and it has no INSERT on decisions | `test_reviewer_cannot_be_named_on_a_decision`, `test_reviewer_is_not_a_person_agents_can_act_for` |
| The reviewer's notes are its own and permanent | Column grant without `written_by` or `written_at`, an insert policy, and the append-only triggers. Each note reaches the audit log | `test_reviewer_writes_a_note_and_a_follow_up`, `test_notes_and_view_log_cannot_be_rewritten_even_by_the_owner` |
| The reviewer traces a proposal to source files without reading `raw` | `ops.raw_origin`, a definer function that returns landing metadata only (table, file, row, batch, checksum), executable by the reviewer alone. The trace is then identical to the pipeline's | `test_reviewer_trace_is_the_same_as_the_pipelines`, `test_raw_origin_returns_metadata_and_no_values` |
| Admin pages show no bank number | The database refuses the column; the pages also mask anything shaped like an IBAN, or under a bank-like key, in stored tool inputs and outputs | `test_no_bank_number_reaches_a_drill_down` |
| Admin page views are recorded | `ops.admin_view_log`, written only through `ops.log_admin_view`, which accepts the reviewer role alone | `test_view_log_records_the_role_and_the_object` |

Application filters stay in place as a second layer. The retrieval tests run them on the superuser login, where the database does nothing, and the security tests run row-level security with those filters removed, so each layer is shown to work alone.

## Limits

- **Sign-in is simulated.** One login assumes roles with `SET ROLE`, and the page's selector picks one. A real deployment would authenticate each person with an identity provider and map them to a role; nothing here does that.
- **`SET ROLE` is not a boundary against code on that connection.** Code that can run arbitrary SQL as `p2p_app` could assume another role. Neither the page nor the agents offer arbitrary SQL, and the model has no SQL tool.
- **The pipeline login is a superuser,** and bypasses all of this. The compose file's default is convenient locally; production would use a dedicated owner with no superuser rights, and `FORCE ROW LEVEL SECURITY`.
- **Neo4j has no row-level security in Community Edition.** The company filter is in the query, and anyone with the Neo4j password can see the whole graph.
- **The admin view log is application-level logging.** The admin app calls `ops.log_admin_view` for each page and object it shows. Anyone who can run SQL as `admin_reviewer` can read the same tables without calling it, so the log shows what the app displayed, not every read. PostgreSQL's own statement logging or `pgaudit` would be the database-level answer; neither is configured here.
- **The reviewer's note and its audit entry are separate rows written in one transaction.** That keeps them consistent, but an owner who drops the triggers could still remove both. The append-only triggers protect against mistakes and ordinary roles, not against the superuser.
- **Masking is at the database.** A row in `raw` or `core` still holds the full number, in the clear, for the pipeline login. There is no encryption at rest beyond what the host provides.
- **Local ports only.** Containers and the page bind to `127.0.0.1`. There is no TLS.

## Retention

Proposed, and not implemented: nothing in this case deletes anything.

| Data | Proposed retention | Why |
| --- | --- | --- |
| `agent.audit_log`, `agent.human_decision` | 7 years | Typical for financial records. The actual period is a legal question for each jurisdiction, and the UK and Germany differ |
| `agent.proposal`, `agent.citation`, `agent.tool_call`, `agent.step` | As the audit log, because they evidence a decision | Tool results copy business data, so they fall under the same access rules |
| `recon` snapshots | Each period's final snapshot as above; intermediate runs 13 months | Comparing runs is mainly useful through the next year-end |
| `raw` | As the source system requires | The source of row-level lineage. Holds bank numbers |
| `docs` | While in force, plus the period above | Rebuildable from source documents |

Append-only triggers and deletion pull against each other. A real implementation would partition the trail by period and drop whole partitions under a controlled, logged, privileged procedure, rather than loosening the triggers.
