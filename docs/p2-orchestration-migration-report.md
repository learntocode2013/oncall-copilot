# P2 orchestration migration report

- **Date:** 2026-10-30
- **Branch:** `p2/meter-plan-solo` (local only, not pushed)
- **Source:** the first three completed todos in the orchestration role plan, previously implemented under `On-Call_Copilot/orchestration`
- **Destination:** `packages/oncall-graph`

Each todo was migrated on its own, then `ruff` and the full `pytest` suite were run. A commit was created only after that suite passed.

| Todo | Commit | Result |
|---|---|---|
| Meter and named ReAct agent | `8a5e25a` `feat(graph): add the L11 meter and named ReAct agent` | 30 passed, 4 skipped |
| Visible plan, `validate_plan`, monitor edges | `dbe1c9b` `feat(graph): add the visible plan and monitor edges` | 41 passed, 4 skipped |
| Solo baseline with the shared gate | `3165ca0` `feat(graph): add the solo baseline behind the shared gate` | 54 passed, 4 skipped |

The four skips are the pending structural guards (eval package, ingestion globs, `CODEOWNERS`, MCP tool schemas). They were already skipped before this work. `uv lock --check` matches the lockfile. Nothing was pushed.

Verified on LangGraph 1.2.18. `interrupt`, `Command(resume=...)`, `InMemorySaver`, and `invoke(..., durability="sync")` behaved as the solo tests expect, including an edit that invalidates the earlier approval. No LangGraph API rewrite was required.

## What landed

`oncall_graph` is a workspace package. Its only public name is `build_graph`.

- `meter.py`, `agent.py`, `llm.py`, `config.py` — per-agent calls, tool counts, largest prompt, tokens, seconds, and model id. The ReAct loop is `llm → action → llm`.
- `state.py`, `planning/plan.py`, `planning/monitor.py` — the runtime plan object, `validate_plan`, and the code edges `retry`, `replan`, `stop`, and `ready`.
- `topologies/c1_single.py`, `nodes/remediate.py`, `stubs.py`, `build.py` — one agent named `solo`, the scripted `paymentFailure` shop, and the shared approval, execute, verify, and rollback path. `build_graph(..., topology="c1_single")` compiles that graph. `c0_workflow` and `c2_team` still raise `NotImplementedError`.

## Major deviations

### 1. The graph has its own `IncidentState`. The shared contract was left alone.

`oncall_contracts.state.IncidentState` is already the week-0 contract. Tests, the memory policy, and the severity vocabulary depend on it. Replacing it would have broken that contract.

The two types differ in ways that will collide when the service writes a run record:

| | Shared contract | Graph channel (`oncall_graph.state`) |
|---|---|---|
| Alert | `dict` (Alertmanager-shaped) | `str` |
| Plan | `list[str]`, optional | one object: stage, hypotheses, read steps, at most one action |
| Hypothesis | `evidence_refs`, `confidence`; the list appends | `evidence` strings, `rank`, `contradicted`; the plan replaces the list |
| Proposed action | field `action`, `rollback` is a string | field `tool`, `rollback` is a string or one allow-listed action |
| Extra graph fields | — | stage, route, retries, revisions, tool budget, meter rows, approval hash, `diagnosis_reports` |

`test_graph_state_does_not_replace_the_shared_contract` locks this split so a later edit cannot overwrite the contract by accident. A mapping between the two still has to be written. Until then, P4 and P5 cannot read the graph channel through the contract they already import.

This also fights the memory policy. That policy says short-term memory is the contract `IncidentState`, and that evidence is stored as references (trace id, query, window), never raw log lines. The graph plan stores evidence strings, and the scripted shop includes a log line. A checkpoint of this state can become a raw-telemetry store.

### 2. Action vocabulary is ahead of the corpus and the tool contract.

The graph allow-list is `set_flag`, `restart`, `scale`, and `rollback`, under the key `tool`. Runbooks and `ProposedAction` use the key `action`. `oncall_knowledge.schema.ACTION_NAMES` currently knows `set_flag` and `none`. `oncall_contracts.tools` does not exist, so the guard that would keep those lists aligned is still skipped.

`restart`, `scale`, and `rollback` can be proposed by the solo agent and will not match a runbook fix until P3 publishes the tool schemas and P1 extends the corpus. The approval payload (command, expected effect, payload hash) lives in `nodes/remediate.py`. The design puts that type in `oncall_contracts.tools`, which P3 owns. It was not invented there, because that module is P3's seam and it does not exist yet.

### 3. Tools, memory, and the checkpointer are still local stand-ins.

`build_graph(tools, store, *, topology)` has the design's arguments, plus `model`, `tool_budget`, `max_revisions`, and `checkpointer`.

- `tools=None` or `tools={}` uses the scripted shop in `stubs.py`. A non-empty tool dict raises `NotImplementedError`. MCP injection is not wired.
- `store` is accepted and unused. Passing a memory store does not retrieve lessons.
- The checkpointer is `InMemorySaver`. Resume works inside the same process. A stopped run does not resume in a later process. `SqliteSaver` is still the open approve-resume todo. The in-process interrupt, hash check, and edit-invalidates-approval behavior came along with the solo baseline, because that is how the shared gate was already written. That does not close the SqliteSaver todo.

`oncall-graph` depends on `oncall-contracts`, LangGraph, and LangChain. It does not declare a dependency on `oncall-knowledge`. `oncall-tools` is not a package yet, so it could not be depended on. The design's arrow is knowledge and tools into the graph. The code does not import either.

### 4. Compiling the real model needs an API key before any call.

`Agent` builds the OpenRouter client in its constructor. `build_graph(topology="c1_single")` with no `model` therefore requires `OPENROUTER_API_KEY` at compile time. CI has no key. Tests pass a scripted model. The service lifespan will need the same choice: inject a model, or accept that process start fails when the key is missing.

Model ids live in `oncall_graph.config` (`openai/gpt-4o-mini` for the solo agent and specialists, `openai/gpt-4.1-mini` for the commander). The design gives that pin to the shared config. That config does not exist yet, so the ids stayed next to the agent that records them on every meter row.

### 5. Recovery is decided on the scripted shop, not on the shop's real metrics.

`verify_metrics` treats recovery as checkout `error_rate < 0.05`. The stub returns `0.08` while `paymentFailure` is on and `0.01` after `set_flag` turns it off. The architecture notes for this repo say there is no payment error counter, frontend series have no `http_route`, and the working signal is HTTP 422s against `demo_payment_transactions_total`. A graph that is correct on the stub can report a false recovery, or no recovery, once it reads Prometheus.

The same stub is what makes `restart` look unrecovered: the flag stays on, verification fails, and code rolls the service back. That path is tested. It is not evidence about the Astronomy Shop.

## Smaller risks

- The meter log is one process-global list, guarded by a lock. Sequential pytest is safe. Running graph tests in parallel workers can mix rows across incidents.
- `new_incident(..., **overrides)` validates the plan argument, then applies overrides afterwards. An override can put an unvalidated plan back into state.
- Shell rejection is a regex over command-shaped fields. Hypothesis evidence is deliberately not scanned, so a log line can quote `docker exec` and still be stored. Action arguments are scanned. This is a filter, not a parser.
- `input_guard_stub` records that it ran. It does not redact secrets or strip instructions. The solo prompt says telemetry is data. Enforcement of that rule is still P3's guardrails.
- `status_draft` is on the read-tool list. The architecture sends status drafts through the tickets server.
- README status still says the agent does not exist. The commander and the specialists still do not. The C1 baseline does, against the scripted shop.
- `docs/contribution-log.md` does not exist, so this work is not in that log.
- The nested repo had no `user.name` / `user.email`. Commits were authored as `morevk <more.vk@gmail.com>` for that command only. Git config was not modified.

## What was not migrated

These plan todos are still open: the team graph and diagnosis fan-out, `SqliteSaver` with a later-process resume, the Reflexion loop and frozen playbook, and the review traces (solo versus team, the signature-A replan trace as a saved artifact). The monitor can already take the replan edge when checkout is blamed and its own report says the cause is not local. That is covered by a unit test, not by a saved review trace.
