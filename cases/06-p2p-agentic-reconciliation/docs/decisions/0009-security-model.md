# 0009 Security model: agents act with a person's database role

Status: accepted

Date: 2 November 2026

## Context

Agents investigate on behalf of finance users and must never see more than that person can. Until now that rested on `company_code` filters in application code, and a missed filter would leak silently. The pipeline's login is a superuser, which bypasses row-level security, and views run as their owner by default, which would bypass it too, so simply adding policies would have protected nothing.

## Options

**How an agent acts for a person:**

1. A session variable holding the user's companies, read by policies. This needs one role, but anything that can run `SET` can change who it is, and the database cannot tell.
2. `SET LOCAL ROLE` to the person's role for each read. The database enforces that role's grants and policies, and the setting ends with the transaction.
3. A separate login per person. This is the real answer in production, and needs an identity provider this case does not have.

**Masking:** a masked view for most roles with full access for a privileged one, or no human role reading the number at all.

## Decision and rationale

- **Option 2, with an application login that has no privileges.** `p2p_app` is `NOINHERIT` and holds nothing until it assumes a role, and it cannot assume the superuser. `agent_service` writes the trail and reads no business data, so a tool that forgot to assume a role would fail rather than leak.
- **Policies use the database's own `current_user`,** looked up in `agent.acting_user`, through a function that stays `SECURITY INVOKER`. Inside a definer function `current_user` would be the owner.
- **Row-level security on every table holding company data, plus catalogue tests** that fail if a later table or view is added without it. A test on the current list of tables would not catch tomorrow's table.
- **Child tables follow their parent** through a policy subquery. Copying `company_code` down would give a second value to keep in step.
- **Decision rights in a policy, not in the page.** The clerk who works the queue cannot clear a failed control, and the auditor cannot decide at all, whichever interface they use.
- **No role reads a full bank number,** not even the controller or auditor. No workflow here needs it, and the verification procedure telephones the number on file.
- **Application filters stay** as a second layer. They also let a denial be told apart from a missing row in the logs, and they are all Neo4j has.

## Consequences and validation

Tests in `tests/test_security.py` connect through `p2p_app` and real roles, because a test on the superuser login would pass with no policies at all. Retrieval latency stayed in the same range with the policies on; see [the evaluation](../../evidence/retrieval-evaluation.md).

The limits are in [security.md](../security.md): sign-in is simulated, the pipeline remains a superuser, and Neo4j relies on its query filter. Editing migration 005 to rename roles broke the rule that an applied migration never changes, which was acceptable only because nothing outside this machine depends on the database; `p2p reset --yes` or new volumes are needed once.
