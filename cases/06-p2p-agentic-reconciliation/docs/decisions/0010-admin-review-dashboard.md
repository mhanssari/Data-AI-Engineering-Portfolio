# 0010 Governance review: a read-only role and a separate admin app

Status: accepted

Date: 3 November 2026

## Context

Someone has to oversee the system itself: how the agents behave across both companies, whether their citations hold, how suppliers were resolved, what the ontology covers, and who may do what. None of the existing roles fits.

- **The controller** can decide failed controls, so an overseer with that role would review their own decisions.
- **The auditor** reads both companies but no `ops` tables, and an audit role that also had to see pipeline internals would mix two jobs.
- **The review page** is built around a queue and a decision, which an overseer must never make.

## Options

1. **The auditor role plus a set of SQL queries.** Nothing new to build or secure, and the simpler choice for a team that is comfortable with SQL. It offers no review notes, no record of what was looked at, and no view of the agent graph or rules. Read access to `ops` would also have to be added to a role whose meaning is "audit the books".
2. **A new read-only role, inside the existing review page.** One app, but every page would have to hide decision controls by role. A missed check would show a reviewer a button the database then refuses.
3. **A new read-only role with its own app.** Chosen.

## Decision and rationale

- **`admin_reviewer` sees everything an overseer needs and decides nothing.**
  - **Sees:** both companies, all documents including audit memos (to check the auditor's own citations), the agent trail, `mdm`, `ops`, the audit log, and stored evaluation results.
  - **Never:** a bank number (only the masked view, as for every role), `raw` (a definer function returns landing metadata without values, so the proof trace still reaches the source file and row), or any write to business data.
  - **Decides nothing:** it is not in `agent.acting_user`. Agents cannot act for it, and the foreign key on `human_decision.decided_by` refuses it whatever any grant says.
- **It is added to the existing visibility functions, not given copies of every policy.** `agent.visible_companies()` and `visible_tags()` also read a new `agent.reviewer` table, so every policy written in migration 006 applies to it unchanged. A copied policy per table would be one more thing to forget for the next table.
- **Its only writes are review notes,** which are append-only and audited. A follow-up is a new note pointing at an earlier one. The column grant leaves out the author and the time, so a note cannot be written in someone else's name or backdated.
- **A separate app,** so no decision control exists for a role check to hide. Every page refuses any other role before running a query. The query logic lives in `src/p2p_recon/admin/`, where it is tested without Streamlit.
- **Rules are view-only.** The page shows the rules from the sources that apply them:
  - the matching SQL, described in `sql/rules.yaml`;
  - the compiled LangGraph graph;
  - the guard's flag catalogue;
  - the offline provider's rule table;
  - the insert policy on decisions.

  Tests compare each description with its source. Editing a rule from a dashboard would let one person change a control and review its effect alone. A governed change would need maker-checker approval by the controller, an effective date, a new version and an impact preview. That design was specified as an optional extension and has not been built.
- **Nothing role-dependent is cached.** Each rerun opens a connection as the selected role. Only files from the repository are cached, and a test fails if any cached admin function takes a connection or role argument.

## Consequences and validation

- **Tests:** the security tests run as the reviewer through the unprivileged login (`tests/test_admin_security.py`). The page tests check that each page refuses `ap_analyst_uk` and the auditor (`tests/test_admin_*.py`).
- **The decision-rights table is checked against the database:** every role tries every decision on every kind of proposal and every lifecycle state.
- **Mutation checks:** rules broken on purpose were caught 18 of 18, 21 of 21 and 18 of 18 across the three slices that added pages.
- **The view log is application-level.** It records what the app showed. Anyone who can run SQL as the reviewer can read without being logged; `pgaudit` would be the database-level answer and is not configured. [security.md](../security.md) says so.
- **Graph scope:** Neo4j Community has no access control, so the reviewer's graph reads are scoped in the query, as the agents' are.
- **Access results are recorded, not inferred.** Pytest leaves no record, so a `p2p access-probe` command tries each role against what it may and may not do and stores each outcome for the Access page.
