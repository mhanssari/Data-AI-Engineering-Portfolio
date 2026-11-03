import streamlit as st

from p2p_recon.admin import agents, rules, ui
from p2p_recon.db import connect_as

role = ui.require_reviewer()

with connect_as(role) as conn:
    ui.log_view(conn, "rules")
    st.caption(
        "Read from what decides: the matching SQL with sql/rules.yaml, the compiled agent graph, the guard, the offline "
        "provider's rule table and the decision policy in PostgreSQL. Rules are view-only here."
    )
    tabs = st.tabs(["Business rules", "Agent workflow", "Guard", "Proposal lifecycle", "Action mapping"])

    with tabs[0]:
        catalogue = rules.catalogue(conn)
        st.dataframe(
            catalogue,
            hide_index=True,
            alt="Every matching and control rule with its reason code, description, scenarios and how often it fired",
        )
        code = st.selectbox("Rule", [r["reason_code"] for r in catalogue], key="rules_rule")
        rule = next(r for r in catalogue if r["reason_code"] == code)
        ui.log_view(conn, "rules", "rule", code)
        st.subheader(f"{rule['label'] or code}")
        st.write(rule["description"])
        st.caption(
            f"SKOS: {rule['kind'] or 'no broader concept'} · definition: {rule['definition'] or 'none in vocab.ttl'} · "
            f"planted in {', '.join(rule['scenarios'])}"
        )
        if rule["parameters"]:
            st.dataframe(
                rules.parameters(conn, rule["parameters"]),
                hide_index=True,
                alt="Policy parameters this rule reads, every version",
            )
        if rule["contract_tolerance"]:
            st.caption(
                "A contract's tolerance replaces the policy default while a contract version is in force."
            )
            st.dataframe(
                rules.contract_tolerances(conn), hide_index=True, alt="Contract price tolerances by version"
            )
        st.subheader("Review notes on this rule")
        ui.notes_panel(conn, "rule", code, key=f"rule-{code}")

        st.subheader("Line status")
        st.caption(
            "Not a state machine: every matching run recomputes every line, so nobody moves a line from one status to "
            "another. The status is whichever test below applies first."
        )
        st.dataframe(
            rules.statuses(conn),
            hide_index=True,
            alt="Line statuses, how each is decided, and current counts",
        )

    with tabs[1]:
        include_evaluation = st.toggle("Offer evaluation runs", value=True, key="rules_evaluation")
        run_rows = agents.runs(conn, agents.RunFilter(include_evaluation=include_evaluation))
        run_id = st.selectbox(
            "Show the path one run took",
            [None, *[r["agent_run_id"] for r in run_rows]],
            format_func=lambda v: "no run: the graph only" if v is None else f"run {v}",
            key="rules_run",
        )
        paths = rules.run_paths(conn, run_id) if run_id else None
        st.graphviz_chart(rules.workflow_dot(paths["edges"] if paths else None), width="stretch")
        st.caption(
            "Drawn from the compiled LangGraph graph. Dashed edges are conditional; notes say what happens inside a node. "
            "Human review is a queue in PostgreSQL, not a node."
        )
        if paths:
            st.dataframe(paths["lines"], hide_index=True, alt="The path each invoice line of this run took")

    with tabs[2]:
        include = st.toggle("Include evaluation runs", value=True, key="rules_guard_evaluation")
        st.caption(
            "Evaluation runs include providers that misbehave on purpose, which is where the guard is exercised."
        )
        checks = rules.guard_checks(conn, agents.RunFilter(include_evaluation=include))
        st.dataframe(
            checks["checks"], hide_index=True, alt="Each guard check, what it does and how often it fired"
        )
        if checks["unknown"]:
            st.error(
                f"Stored flags with no description: {', '.join(checks['unknown'])}",
                title="Undocumented flags",
            )

    with tabs[3]:
        st.graphviz_chart(rules.lifecycle_dot(), width="stretch")
        st.caption(
            "Any decision, a query included, takes a proposal off the queue. The database accepts a further decision on a "
            "decided proposal, and on one that was never queued; the latest decision counts."
        )
        st.subheader("Who may record a decision")
        st.dataframe(
            rules.decision_rights(conn),
            hide_index=True,
            alt="Which roles may decide which proposals, per company",
        )
        policy = rules.decision_policy(conn)
        st.caption(
            f"Failed controls ({', '.join(policy['control_reasons'])}) may be decided only by "
            f"{', '.join(policy['privileged_roles'])}. Read from the policy below."
        )
        for row in policy["policies"]:
            st.code(
                f"-- {row['policyname']} ({row['cmd']}, {', '.join(row['roles'])})\n"
                f"using: {row['qual']}\nwith check: {row['with_check']}",
                language="sql",
            )

    with tabs[4]:
        st.caption(
            "The offline provider's rule table, in the order it applies, beside the evaluation's expected actions. A live "
            "model chooses its own action within what the guard allows."
        )
        st.dataframe(
            rules.action_mapping(), hide_index=True, alt="Proposed action for each reason code and status"
        )
