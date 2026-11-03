import streamlit as st

from p2p_recon.admin import agents, audit_pack, ui
from p2p_recon.db import connect_as

role = ui.require_reviewer()


def percent(value: float | None) -> str:
    return "–" if value is None else f"{value:.1%}"


with connect_as(role) as conn:
    ui.log_view(conn, "agents")
    options = agents.filter_options(conn)

    with st.container(horizontal=True, vertical_alignment="bottom"):
        dates = st.date_input("Started between", value=(), key="agents_dates")
        providers = st.multiselect("Provider", options["providers"], key="agents_providers")
        users = st.multiselect("Acting user", options["users"], key="agents_users")
        companies = st.pills("Company", options["companies"], selection_mode="multi", key="agents_companies")
        outcomes = st.multiselect("Run outcome", options["outcomes"], key="agents_outcomes")
        include_evaluation = st.toggle("Include evaluation runs", key="agents_evaluation")
    date_from, date_to = (tuple(dates) + (None, None))[:2] if dates else (None, None)
    run_filter = agents.RunFilter(
        date_from=date_from,
        date_to=date_to or date_from,
        providers=tuple(providers),
        users=tuple(users),
        companies=tuple(companies or ()),
        outcomes=tuple(outcomes),
        include_evaluation=include_evaluation,
    )
    figures = agents.kpis(conn, run_filter)
    run_rows = agents.runs(conn, run_filter)

    if not include_evaluation:
        st.caption(
            "Evaluation runs are excluded: they include providers that misbehave on purpose. Switch them on to see them."
        )
    guard, decided, cited = figures["guard"], figures["decisions"], figures["citations"]
    with st.container(horizontal=True):
        st.metric("Runs", len(figures["run_ids"]), border=True)
        st.metric("Proposals", guard["proposals"], border=True)
        st.metric("Agreement with people", percent(decided["agreement_rate"]), border=True)
        st.metric("Guard interventions", percent(guard["rate"]), border=True)
        st.metric("Citation validity", percent(cited["validity"]), border=True)
    st.caption(
        "Agreement is ACCEPT ÷ (ACCEPT + OVERRIDE) on the latest decision per proposal; QUERY counts for neither. "
        "A guard intervention is a flagged draft or a changed action. A valid citation resolves and is readable by the "
        "run's acting user."
    )

    left, right = st.columns(2)
    with left:
        st.subheader("Proposals by action and SQL status")
        st.dataframe(figures["proposals"], hide_index=True, alt="Proposals by proposed action and SQL status")
        st.subheader("Human decisions")
        st.dataframe(
            [{"decision": k, "proposals": v} for k, v in decided["counts"].items()],
            hide_index=True,
            alt="Latest human decision per proposal, by kind",
        )
        st.subheader("Guard interventions by reason")
        st.caption(f"{guard['action_changed']} proposals had their drafted action changed by the guard.")
        st.dataframe(guard["reasons"], hide_index=True, alt="Guard flags by reason and how often each fired")
        st.subheader("Outage and limits")
        outage = figures["outage"]
        st.write(
            f"{outage['provider_failures']} provider failures and {outage['limit_reached']} step or token limits; "
            f"{outage['routed_to_person']} of {outage['affected']} affected lines went to a person."
        )
        st.caption(
            "Server-side refusal fallback on the anthropic provider is not recorded separately; it would appear here "
            "only through the model name on the run."
        )
    with right:
        st.subheader("Tool calls")
        st.dataframe(
            figures["tools"], hide_index=True, alt="Tool calls by tool, with status and median latency"
        )
        st.subheader("Latency per agent")
        st.dataframe(figures["latency"], hide_index=True, alt="Step latency p50 and p95 per agent")
        st.subheader("Tokens and cost per provider")
        st.caption("The offline provider is a rule table, so its tokens and cost are zero, not missing.")
        st.dataframe(figures["usage"], hide_index=True, alt="Tokens and cost per provider and model")

    st.subheader("Runs")
    st.dataframe(run_rows, hide_index=True, alt="Agent runs matching the filter")
    range_pack = audit_pack.range_pack(conn, run_filter)
    range_name = audit_pack.default_name("audit-pack", run_filter.date_from, run_filter.date_to)
    with st.container(horizontal=True):
        st.download_button(
            "Audit pack for these runs (markdown)",
            range_pack["markdown"],
            file_name=f"{range_name}.md",
            mime="text/markdown",
            key="agents_range_md",
        )
        st.download_button(
            "Audit pack for these runs (CSV)",
            range_pack["csv"],
            file_name=f"{range_name}.csv",
            mime="text/csv",
            key="agents_range_csv",
        )
    if not run_rows:
        st.info(
            "No agent runs match the filter. Run `uv run p2p agent-run --as ap_analyst_uk` to create one."
        )
        st.stop()

    st.header("One run")
    run_ids = [r["agent_run_id"] for r in run_rows]
    requested = st.query_params.get("run")
    start = (
        run_ids.index(int(requested))
        if requested and requested.isdigit() and int(requested) in run_ids
        else 0
    )
    run_id = st.selectbox("Agent run", run_ids, index=start, key="agents_run")
    ui.log_view(conn, "agents", "agent_run", run_id)
    lines = agents.run_lines(conn, run_id)
    st.dataframe(lines, hide_index=True, alt="Invoice lines this run looked at, with their proposals")
    labels = {
        f"{line['source_system']} {line['invoice_id']} line {line['line_number']}"
        + (f" · {line['proposed_action']}" if line["proposed_action"] else " · no proposal"): line
        for line in lines
    }
    if not labels:
        st.info("This run looked at no lines.")
        st.stop()
    chosen = labels[st.selectbox("Invoice line", list(labels), key="agents_line")]
    detail = agents.line_detail(
        conn, run_id, chosen["source_system"], chosen["invoice_id"], chosen["line_number"]
    )

    st.subheader("Step timeline")
    st.dataframe(
        [
            {
                "step": s["step_index"],
                "agent": s["agent_name"],
                "started": s["started_at"],
                "latency_ms": s["latency_ms"],
                "tokens": (s["input_tokens"] or 0) + (s["output_tokens"] or 0),
                "error": s["error"],
            }
            for s in detail["steps"]
        ],
        hide_index=True,
        alt="Each step for this invoice line in order",
    )
    st.subheader("Tool calls")
    st.caption("Inputs and outputs are shown as stored, with anything shaped like a bank detail masked.")
    for call in detail["tool_calls"]:
        with st.expander(f"{call['tool_name']} · {call['status']} · {call['latency_ms']} ms"):
            st.json({"arguments": call["arguments"], "result": call["result"]}, expanded=False)

    proposal = detail["proposal"]
    if proposal is None:
        st.info("No proposal: the acting user may not see this line, so the supervisor stopped.")
        st.stop()
    ui.log_view(conn, "agents", "proposal", proposal["proposal_id"])
    st.subheader(f"Proposal {proposal['proposal_id']}")
    st.markdown(
        f"**Drafted:** `{proposal['drafted_action']}` · **Stored:** `{proposal['proposed_action']}` · "
        f"**SQL:** {proposal['sql_status']} {', '.join(proposal['sql_reasons']) or ''}"
    )
    if proposal["guard_flags"]:
        st.warning("Guard flags: " + ", ".join(proposal["guard_flags"]))
    # Model-written text, so it is shown as plain text and never rendered as markdown.
    st.text(proposal["explanation"])

    st.subheader("Citations")
    for citation in detail["citations"]:
        with st.expander(f"{citation['kind']}: {citation['reference']}"):
            if citation["kind"] == "chunk":
                if citation["chunk"] is None:
                    st.write("This chunk no longer exists.")
                else:
                    st.caption(f"{citation['chunk']['file_name']} · {citation['chunk']['context_header']}")
                    st.text(citation["chunk"]["content"])  # Third-party text: never rendered as markdown.
            else:
                st.json(citation["key"])
                st.json(
                    {k: str(v) for k, v in (citation["row"] or {}).items()} or {"row": "does not resolve"}
                )

    st.subheader("Human decisions")
    if detail["decisions"]:
        st.dataframe(detail["decisions"], hide_index=True, alt="Human decisions on this proposal")
    else:
        st.write("No decision yet.")

    trace = agents.trace_markdown(conn, proposal["proposal_id"])
    with st.expander("Full trace, as `p2p trace` writes it"):
        # It quotes document text, which is third-party, so it is shown as source and never rendered.
        st.code(trace, language="markdown")
    st.download_button(
        "Download trace",
        trace,
        file_name=f"trace-{proposal['proposal_id']}.md",
        mime="text/markdown",
        key="agents_trace_download",
    )
    pack = audit_pack.proposal_pack(conn, proposal["proposal_id"])
    with st.container(horizontal=True):
        st.download_button(
            "Audit pack (markdown)",
            pack["markdown"],
            file_name=f"audit-pack-proposal-{proposal['proposal_id']}.md",
            mime="text/markdown",
            key="agents_pack_md",
        )
        st.download_button(
            "Audit pack (CSV)",
            pack["csv"],
            file_name=f"audit-pack-proposal-{proposal['proposal_id']}.csv",
            mime="text/csv",
            key="agents_pack_csv",
        )

    st.subheader("Review notes on this proposal")
    ui.notes_panel(conn, "proposal", proposal["proposal_id"], key=f"proposal-{proposal['proposal_id']}")
