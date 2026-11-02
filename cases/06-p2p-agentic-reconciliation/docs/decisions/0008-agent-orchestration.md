# 0008 Agent orchestration and enforcement

Status: accepted

Date: 2 November 2026

## Context

Exceptions need investigating and explaining, without an agent ever being able to post, pay, change a status or approve a held invoice. The design has to hold when the model is wrong, down, or persuaded by a supplier's email to do something else.

## Options

**Orchestration.** A plain Python state machine would be about forty lines with no new dependency. LangGraph 1.2.19 offers a typed state graph, conditional routing and checkpointing, and was quick to adopt: a spike ran on Windows and Python 3.12 first time.

**How much the model decides.** It could drive every step, choosing tools freely, or be confined to the one step that needs judgement.

**Enforcement.** Instructions in a prompt, checks in Python, or database privileges.

## Decision and rationale

- **LangGraph, `StateGraph` only,** with our own provider layer and none of LangChain's model wrappers. An honest assessment: this flow is simple enough that a plain state machine would serve as well, and it brings a sizeable transitive dependency tree, including `langchain-core` and `langsmith`. It is used because it is straightforward here and because the graph states the routing explicitly. LangSmith tracing is forced off in code, since it would send prompts and documents off the machine.
- **Only the exception investigator calls a model.** Supervisor, controls and matching are agents in the sense of having their own job and tools, but what they do is fully specified, so a model would add cost and risk and nothing else. A clean line never reaches a model.
- **Human review is a queue in PostgreSQL** rather than a paused graph or a LangGraph checkpoint. A finance user's answer can take days, and needs no process kept alive for it.
- **`recon.run_matching` is not a tool.** It writes a new snapshot, so the matching agent reads current results instead.
- **Three layers of enforcement, none of them the prompt:**
  1. **Database role.** Tools run as `p2p_agent` (`NOLOGIN`, assumed with `SET ROLE`, so no new credential). It can read `core`, `recon`, `docs` and part of `mdm`, and insert into the agent trail. It has no write access elsewhere, no access to `raw` or `ops`, no privilege on `agent.human_decision`, and a column grant that leaves out the bank account number.
  2. **Guard.** Every draft passes `apply_guard` before it is stored. `AUTO_APPROVE` is allowed only if SQL shows a clean status with no reasons or breaches. Citations must have been returned by a tool in this run. An uncited draft, an unknown action or an unknown tool becomes `NEEDS_HUMAN_REVIEW`. An explanation quoting a figure no tool returned is withheld. The model's draft is stored beside the outcome, so a blocked attempt stays visible.
  3. **Append-only trail.** Triggers refuse updates and deletes on proposals, citations and decisions, even for the table owner.
- **Bounded and failing safe.** Four model steps and 40,000 tokens per line. A timeout, refusal, malformed reply or exhausted budget ends in human review.
- **`offline` is the default provider,** so tests and the demo need no key. It is a rule table, not a model.
- **Claude requests use structured output and server-side refusal fallback** (`fallbacks: "default"`), which can run a declined request on another model. Retrieved text is fenced as untrusted in the prompt, and cannot close its own fence.

## Consequences and validation

- Zero false auto-approvals is meaningful only if it can move. The evaluation runs a hostile provider that attacks the guard, and a test disables the guard and confirms that the same run then reports false approvals.
- The default evaluation measures the pipeline and guardrails, not a model's judgement. **The `anthropic` and `ollama` providers have been run only against mocked responses:** request shape and response parsing are tested, but no live model has been evaluated, and token and cost figures so far are estimates or zero.
- The role does not stop a bug in the guard; the guard does not stop a bad SQL grant. Company scoping in tools is application code until row-level security in milestone 3.
- The set of allowed actions lives in a Python tuple and a database check. A test compares them, though a controlled vocabulary in the ontology would be better.
- Prompt injection is mitigated, not solved. A model could still be misled into a wrong *held* action or a poor explanation; what the design prevents is that turning into an approval or a payment. A person reviews every proposal on a held line.
