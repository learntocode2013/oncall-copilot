# On-Call Graph contracts

- **Date:** 2026-11-01
- **Owner:** P2 — orchestration, planning and improvement
- **Status:** The interface other packages build against
- **Package:** `packages/oncall-graph` (`oncall_graph`)

This page is the boundary of the incident graph. Two directions:

1. **Out.** What `oncall-graph` exposes, and the data other components may read from a run.
2. **In.** What `oncall-graph` requires from the other components before it can run.

Shared names that more than one package must import live in `oncall-contracts`. The graph does not grow a second public type package. Evaluation never imports the graph.

## Rules

1. **`build_graph` is the only public Python name.** `from oncall_graph import build_graph`. Nodes, the planner, the meter, the scripted shop, and `oncall_graph.state.IncidentChannel` are internal and may change.
2. **The service is the only workspace package that imports the graph.** Knowledge and tools are injected. The evaluation harness posts an alert to the incident API and reads a `RunRecord`. It does not import `oncall_graph`.
3. **The graph does not import `oncall_eval`, an MCP server, or the shop client.** Tools and the memory store arrive as arguments.
4. **The model proposes. Code and a person decide.** Severity, the tool allow-list, the tool-call budget, validation, rollback, and the approval hash check are Python. The model chooses among those rules and explains the choice.
5. **One action.** A plan that names two writes, an unknown tool, a shell-shaped command, or a write with no expected effect is rejected. Nothing in a rejected plan runs.
6. **Approval binds to the exact payload.** The hash covers the action name, its arguments, and the expected effect. An edit interrupts again. A reject writes nothing.
7. **Evidence that leaves the process is a reference.** Trace id, PromQL text, time window. Raw log lines stay inside a tool result for the current turn. They are not the checkpoint field other packages persist. See `docs/memory-policy.md`.
8. **One shared incident.** The fields on `oncall_contracts.state.IncidentState` are that incident. Graph bookkeeping stays off that type. The compiled solo order is `docs/oncall-graph-solo.md`.

```mermaid
flowchart LR
  contracts["oncall-contracts"]
  knowledge["oncall-knowledge<br/>retriever and lesson store"]
  tools["oncall-tools<br/>tool registry and hooks"]
  graph["oncall-graph<br/>build_graph"]
  service["oncall-service<br/>incident API"]
  eval["oncall-eval<br/>HTTP only"]

  contracts --> graph
  knowledge -->|"store"| graph
  tools -->|"tools"| graph
  graph -->|"compiled graph"| service
  service -->|"RunRecord and alert"| eval
  contracts --> service
  contracts --> eval
  contracts --> knowledge
  contracts --> tools
```

## Who calls the graph

| Component | May import `oncall_graph` | How it meets the graph |
|---|---|---|
| `oncall-service` (P5) | yes, `build_graph` only | Builds the graph once at startup. Invokes and resumes it. Maps the run onto `IncidentState` and `RunRecord`. |
| `oncall-tools` (P3) | no | `load_tools(transport)` is passed in. |
| `oncall-knowledge` (P1) | no | Retriever and lesson store are passed in as `store`. |
| `oncall-eval` (P4) | no | `POST /alerts`, then the run record under `artifacts/runs/`. |
| `oncall-contracts` | no | Imported by the graph. Never the other way around. |

`oncall_contracts.config.Topology` is how a caller names a configuration without importing the graph: `c0_workflow`, `c1_single`, `c2_team`.

## 1. What the graph exposes

### 1.1 Compile

```python
def build_graph(
    tools: ToolRegistry | None = None,
    store: LessonStore | None = None,
    *,
    topology: Topology = "c2_team",
    model: ChatModel | None = None,
    model_ids: ModelIds | None = None,
    tool_budget: Literal[5, 10, 20] = 10,
    max_revisions: int = 3,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph: ...
```

| Argument | Meaning |
|---|---|
| `tools` | Registry from `load_tools`. `None` is allowed only for a local scripted run. |
| `store` | Long-term lesson store. The live incident is not written here. |
| `topology` | Which E1 graph to compile. All three stay callable for the life of the project. Adding C2 does not change C1. |
| `model` | Ready-made chat model. When it is omitted, the service has already configured the client the graph will call. |
| `model_ids` | Commander id and specialist id. Every meter row copies the id it actually called. |
| `tool_budget` | Incident cap. The solo agent and the team receive the same number. Parallel diagnosis splits it; the slices sum to the cap. |
| `max_revisions` | Replans allowed before the run escalates. |
| `checkpointer` | Saver whose thread id is the incident id. A run that must resume in a later process is given a durable saver by the service. |

`c0_workflow` and `c2_team` are part of the signature now. Until those topologies are added, calling them raises `NotImplementedError`. `c1_single` compiles.

The service builds this object once and reuses it. It does not construct nodes itself.

### 1.2 Start and resume

```python
config = {"configurable": {"thread_id": incident_id}}

# start
graph.invoke(initial, config)

# resume after an interrupt
graph.invoke(Command(resume=decision), config)
```

`incident_id` is the checkpointer thread id. One incident, one thread.

`initial` is the external incident, not the graph's private bookkeeping. The service passes:

| Field | Type | Source |
|---|---|---|
| `incident_id` | `str` | Created by the service when the alert is accepted. |
| `alert` | alert object, §2.1 | `POST /alerts`, already validated. |
| `tool_budget` | `5 \| 10 \| 20` | Run configuration. Same number C1 and C2 use. |
| `max_revisions` | `int` | Run configuration. Default 3. |

A return value with `__interrupt__` is paused. The service reads the interrupt value in §1.4 and does not treat the run as finished. A return value without `__interrupt__` is finished when `status` is terminal (§1.5).

### 1.3 Shared incident

`oncall_contracts.state.IncidentState` is the incident other packages import. The graph writes these fields in this shape. A later `plan` replaces the earlier one. `hypotheses`, `evidence`, and `transcript` append, so parallel diagnosis copies do not wipe each other out.

| Field | Type | Present | Who consumes it |
|---|---|---|---|
| `alert` | §2.1 | always | The service keeps the object it validated. |
| `scope` | `{services: list[str]}` | after triage | UI. Suspect services only. Severity is its own field. |
| `severity` | `sev1 \| sev2 \| sev3 \| none` | after triage | UI. Assigned from `docs/severity-policy.md`. |
| `hypotheses` | list of `{service, statement, evidence_refs, confidence}` | always, may be empty | UI. The harness compares the list with the fault. |
| `evidence` | list of `{kind, ref, source}` | always, may be empty | Memory policy. `kind` is `promql`, `trace`, `log_query`, or `change`. |
| `plan` | §1.6 | after a plan exists | UI. Stage and remaining reads. |
| `proposed_action` | §2.3 | after a proposal | Approval and the UI. One write. |
| `verification` | §1.7 | after verify | UI. Run record `verified_recovery`. |
| `transcript` | list of `{kind, text}` | always, may be empty | UI timeline. |

`evidence_refs` on a hypothesis point at `evidence`. They are not log lines. A hypothesis has no rank and no contradicted flag. Confidence is the model's weight for that statement.

Three more channel values are not incident fields, and the service still reads them:

| Field | Type | Who consumes it |
|---|---|---|
| `status` | §1.5 | Incident API. |
| `meter_rows` | list of §1.8 | Run record cost columns. Append-only. |
| `tool_calls` | `int` | Run record `tool_calls`. |

`incident_id` is the thread id the service created. The graph stores a copy so the checkpoint matches. Other packages do not import it from the graph.

These keys stay on the graph channel. No other package needs them:

| Field | What the graph uses it for |
|---|---|
| `last` | Latest tool call and its result. The monitor reads this. |
| `ok` | Whether that latest call failed. |
| `route` | Next edge: `retry`, `replan`, `stop`, or `ready`. |
| `retries` | Timeouts already retried for the current call. |
| `revisions` | Replans already applied. |
| `max_revisions` | Cap. The service passes it in and does not read it back. |
| `tool_budget` | Cap the graph enforces. The service already has the number it configured. |
| `why` | Monitor reason for the current edge. The engineer-facing line is `transcript`. |
| `pending_rollback` | Inverse code will apply. `RunRecord` only needs `rolled_back`. |
| `diagnosis_reports` | §1.9. Per-suspect verdicts. |
| `approval_payload_hash` | Execute compares it with the command. The service already has the hash from §1.4. |
| `stage` | Copy of `plan.stage`. The plan field is the one other packages read. |

The LangGraph type is `oncall_graph.state.IncidentChannel`. It carries the nine shared fields plus this private set, and `status`, `meter_rows`, and `tool_calls`. Packages other than the service do not read the checkpoint. §4 lists what C1 has not caught up to. The solo event order is `docs/oncall-graph-solo.md`.

### 1.4 Approval interrupt

When the graph pauses, `__interrupt__[0].value` is:

| Field | Type | Required |
|---|---|---|
| `command.action` | `set_flag \| restart \| scale \| rollback` | yes |
| `command.args` | object, §2.3 | yes |
| `expected_effect` | non-empty string | yes |
| `payload_hash` | hex SHA-256 of the canonical command | yes |
| `error` | string | only when the previous resume was invalid |

The hash is SHA-256 over the UTF-8 JSON of `{action, args, expected_effect}` with sorted keys and no extra whitespace. `args` key order does not change the hash.

The service shows that object to the engineer and resumes with one of:

| Decision | Resume value | Graph result |
|---|---|---|
| Approve | `{"decision": "approve", "payload_hash": "<the hash just shown>"}` | Runs that command. The private channel stores the hash for execute. |
| Reject | `{"decision": "reject"}` | `status` `REJECTED`. No shop write. |
| Edit | `{"decision": "edit", "command": {"action", "args", "expected_effect"}}` | Does not run the old command. Interrupts again with the new command and a new hash. |
| Stale hash | `{"decision": "approve", "payload_hash": "<an older hash>"}` | Interrupts again. No shop write. |

`approve` and `approved` are the same decision. `reject` and `rejected` are the same decision. Any other `decision` leaves the node paused.

### 1.5 Status

| Status | Terminal | Meaning |
|---|---|---|
| `RUNNING` | no | Investigation or approval is in progress. Check `__interrupt__` before deciding it is idle. |
| `APPROVED` | no | Hash matched. The write has been requested. |
| `RETRY` | no | A tool timed out once. The same call is queued again. |
| `REPLAN` | no | The plan went back to `hypotheses`. |
| `READY` | no | The monitor found nothing to correct. |
| `RECOVERED` | yes | Verification measured recovery. |
| `REJECTED` | yes | The engineer rejected the command, or there was nothing to approve. |
| `ESCALATED` | yes | Budget or revision cap spent, or the agent proposed no action. |
| `APPROVAL_MISMATCH` | yes | Execute refused because the stored hash does not match the command. No shop write. |

### 1.6 Visible plan

The plan is the stage and the reads still intended. Hypotheses and the one write are sibling fields on the incident (§1.3).

| Field | Type |
|---|---|
| `stage` | `scope`, `hypotheses`, `test`, `act`, `verify`, or `communicate` |
| `steps` | list of `{tool, args}` using read-tool names only |

| Stage | What the run is doing |
|---|---|
| `scope` | Settling which services are suspects |
| `hypotheses` | Forming explanations |
| `test` | Carrying out the reads in `steps` |
| `act` | A write has been proposed |
| `verify` | Checking whether the shop recovered |
| `communicate` | Drafting the incident note |

`steps[].tool` is `promql`, `log_search`, `traces`, `recent_changes`, `search_runbooks`, `incident_record`, or `status_draft`. `set_flag`, `restart`, `scale`, and `rollback` are not steps.

`validate_plan` is internal. The service does not call it. A plan that fails it never becomes `proposed_action`.

### 1.7 Verification

| Field | Type | Meaning |
|---|---|---|
| `recovered` | `bool` | Code measured the success signal. The model's confidence is not this field. |
| `signal` | `str` | Name of the check that was applied. |
| `evidence_refs` | `list[str]` | Queries or trace ids that justified `recovered`. |

`rolled_back` on the run record is true when code applied a rollback. The inverse itself stays in private `pending_rollback`.

### 1.8 Meter row

One row per model call. Dashboards and the run record aggregate these. They do not scrape logs.

| Field | Type |
|---|---|
| `agent` | `solo`, `commander`, `triage`, `diagnosis:<service>`, `runbook`, `remediation`, or `comms` |
| `tools_available` | list of tool names that agent was allowed to call |
| `tools_invoked` | list of tool names that call actually ran |
| `input_tokens` | int |
| `output_tokens` | int |
| `model_id` | OpenRouter id used for that call |
| `seconds` | that call, including the tool round that followed it |

The run record rolls these up:

| Run record field | Aggregation |
|---|---|
| `tool_calls` | sum of tools invoked, also equal to the state's `tool_calls` |
| `tokens` | sum of input and output tokens |
| `latency_ms` | wall-clock of the run, not the sum of overlapping agents |
| `model_ids` | `{agent: model_id}` last id seen for that agent |
| `usd` | filled by the service from the token counts and the priced model table. The graph does not invent a price. |

The largest prompt is max `input_tokens` overall and per agent. The service keeps it on the run artifact even though `RunRecord` has no column for it yet. That figure is what separates one agent from a team.

### 1.9 Diagnosis report

Private. One envelope per diagnosis copy. The list appends, because `last` keeps only the newest tool result and a later monitor pass must still see every copy.

| Field | Type |
|---|---|
| `service` | suspect the copy was given |
| `local_cause` | `bool`. `false` means this service's evidence denies a local root cause |
| `tool_calls` | how many of its slice it spent |

The copy's statements append to contract `hypotheses`. The report does not keep a second hypothesis list. A denial with no statement is not a hypothesis, so this envelope stays off `Hypothesis`. Solo leaves the list empty. The team fan-out fills it. The shared monitor reads `local_cause` for signature A: the run blamed a service, and that service's own report says the cause is not local.

### 1.10 What the service persists

The graph is held to writing §1.3 in this shape. §4 is where C1 has not caught up. The service returns that incident and fills `RunRecord` from `status`, `meter_rows`, and `tool_calls`.

| `IncidentState` field | Written by |
|---|---|
| `alert` | The service, before invoke. The graph does not replace the validated object. |
| `scope` | Triage. Empty on C1, which has no triage node. |
| `severity` | Triage, from `docs/severity-policy.md`. Empty on C1. |
| `hypotheses` | Each diagnosis copy appends its statements. On the current solo path, `propose_one_action` derives one hypothesis in Python from the proposal. |
| `evidence` | Each read appends one reference. |
| `plan` | The current stage and the reads still intended. |
| `proposed_action` | The one proposal. Key `action`. `rollback` is a procedure string. |
| `verification` | The verify node, §1.7. |
| `transcript` | Each node appends `{kind, text}`. |

| `RunRecord` | Taken from |
|---|---|
| `config_id` | the `topology` passed to `build_graph` |
| `complexity` | supplied by the harness from the fault, not guessed by the model. `single` when one root-cause service, `multi` otherwise. |
| `tool_calls`, `tokens`, `latency_ms`, `model_ids` | §1.8 |
| `correct_root_cause`, `root_cause_rank`, `correct_action_proposed`, `harmful_actions` | the harness, comparing the run with the fault manifest. The graph does not set these. |
| `verified_recovery` | `verification.recovered` |
| `rolled_back` | a rollback action was applied |
| `playbook_hash` | hash of the frozen playbook the runbook step read. Empty until that loop exists. |

## 2. What the graph requires

### 2.1 Alert, from the service

P5 validates one alert schema for both Alertmanager and the fault injector. `incident_id` is the thread id beside this object, not a field of it. The graph reads:

| Field | Required | Use |
|---|---|---|
| `status` | yes | `firing` starts a run. `resolved` does not start remediation. |
| `labels.alertname` | yes | Shown on the plan and the brief. |
| `labels.service` | yes | First suspect. Triage may add more. |
| `labels.severity` | no | A hint. The severity policy overrides it. |
| `annotations.summary` | yes | The brief. Treated as data, never as instructions. |
| `annotations.description` | no | Extra data. |
| `startsAt` | yes | Window for PromQL and traces. |

The original object is kept on the external `IncidentState.alert`. The graph does not require a shell, a callback URL, or a pre-chosen root cause.

### 2.2 Model configuration, from the service

| Name | Required value |
|---|---|
| Commander model | A stronger OpenRouter id than the specialists. `openai/gpt-4.1-mini` is the current pin. |
| Specialist model | `openai/gpt-4o-mini`. Solo uses this id, because C1 has no commander. |
| Temperature | `0` |
| Judge model | Not passed to the graph. The harness uses a different model family. |

Both ids are recorded on meter rows. The service owns the pin so C0, C1, and C2 stay comparable. The graph reads them from `model_ids`, not from a private copy, once that object is in the shared config.

### 2.3 Actions, from contracts and tools

One write, typed arguments, an expected effect, and a rollback.

| Action | Arguments | Default rollback |
|---|---|---|
| `set_flag` | `flag: str`, `variant: Variant` | `rollback_flag` of the same flag, restoring the `previous` variant the write reported — see [ADR-0005](decisions/0005-one-set_flag-payload.md) |
| `restart` | `service: str` | `rollback` of that service |
| `scale` | `service: str`, `replicas: int` (`>= 1`) | `rollback` of that service |
| `rollback` | `service: str` | the procedure names that service; it does not nest another rollback |

On `ProposedAction`, `rollback` is that procedure as a string. The structured inverse, when code must apply one, stays in private `pending_rollback`.

Names match `^[A-Za-z_][A-Za-z0-9_-]*$`. `command`, `cmd`, `shell`, `script`, and `exec` are rejected as argument names. There is no shell tool.

This list is wider than `oncall_knowledge.schema.ACTION_NAMES`, which currently has `set_flag` and `none`. The shared list belongs in `oncall_contracts.tools` when P3 adds it. Until then the graph allow-list above is the list a proposal must use. `none` is a runbook statement, not an action the graph executes.

The command key is `action`, matching `ProposedAction` and runbook front matter. `rollback` on that object is the procedure string. The interrupt hashes `{action, args, expected_effect}`.

### 2.4 Tool registry, from tools

`load_tools(transport) -> ToolRegistry` with `transport` of `stdio` or `http`. The graph cannot tell which one it was given.

Each tool entry:

| Field | Required |
|---|---|
| `name` | one of §2.5 |
| `description` | string the model sees |
| `access` | `read` or `write` |
| `invoke(args) -> dict` | JSON-shaped result. Exceptions become a failed tool result, not a stack trace in the prompt. |
| `parameters` | the names in §2.5, so the model cannot invent a `command` argument |

Per-agent allow-list the registry must already enforce before a call reaches the shop:

| Agent | Tools |
|---|---|
| Triage | `promql`, `log_search`, `traces`, `recent_changes`. Read only. |
| Diagnosis, one copy per suspect | `promql`, `log_search`, `traces`, `recent_changes`. Read only. No write tool is present on that copy. |
| Runbook | `search_runbooks`, plus the lesson store. Read only. |
| Comms | `incident_record`, `status_draft`. Drafts are not sent by the tool. |
| Remediation | Proposal schemas for the four actions. The proposal call does not change the shop. |
| Solo (C1) | Every read tool and the four proposal schemas. The same approval node still gates the write. |

Write implementations (`set_flag`, `restart`, `scale`, `rollback`) are callable by the approval node after the hash matches. They are not callable by the model. A diagnosis registry that contains any `access: write` tool is a broken injection, and the service must refuse to compile that graph.

Tool results are data. A log line that says `docker exec` is evidence text. It is not a tool call.

Budgets and the loop breaker live in the pre-tool hook (P3). The graph also stops an agent at its own slice of `tool_budget`, so a missing hook cannot give the team N times the solo budget.

### 2.5 Tool results the graph reads

| Tool | Arguments | Result fields the graph reads |
|---|---|---|
| `promql` | `query: str` | the series payload, plus the query echoed back |
| `log_search` | `service: str`, `query: str` | `service`, `query`, `lines` |
| `traces` | `service: str` | `service`, `local_cause`, `evidence` as references |
| `recent_changes` | `service: str` | `changes`: flag and deploy events |
| `search_runbooks` | `symptom: str` | `passages`: `id`, `service`, `symptom`, `preconditions`, `fix` |
| `incident_record` | `incident_id: str` | `incident_id`, `events`. This call does not append. |
| `status_draft` | `note: str` | `draft`, `sent: false` |
| write tools | §2.3 | `{ok: true, action, args}` after the approved apply |

A timeout is a result the monitor can see (`timeout: true` or an error whose text says the call timed out). The monitor retries that call once, then replans. The model does not choose the edge.

### 2.6 Lesson store, from knowledge

`store` is the long-term side of `docs/memory-policy.md`. The graph asks it to search. The graph does not create the index and does not write a held-out lesson into it.

Search input:

| Field | Meaning |
|---|---|
| `service` | suspect or the service on the top hypothesis |
| `symptom` | alert summary or the runbook symptom string |
| `status` | `approved` only, for lessons the agent may apply |

Each hit:

| Field | Meaning |
|---|---|
| `id` | stable id |
| `text` | the lesson the runbook step may show the model |
| `preconditions` | service, symptom, and when it applies |
| `status` | `approved` or, if a caller asks for the review set, `pending` or `rejected` |
| `rejection_reason` | present when `status` is `rejected` |

The runbook step drops a lesson whose preconditions do not match the case. A rejected lesson is never passed to the model as guidance. Pending lessons stay out until a person accepts them.

The graph writes a candidate lesson only through the review step, as `pending`, with `preconditions` and the mistake it came from. Accepted lessons are what a later run can retrieve. The frozen `playbook.json` is read-only on held-out runs; the graph must not append to it. The hash of that file is what the service copies into `RunRecord.playbook_hash`.

### 2.7 Checkpointer and resume transport, from the service

The service passes the saver into `build_graph` and keeps it for the process lifetime. Resume from the HTTP API is `Command(resume=decision)` on the same `thread_id`. The graph does not read the approval HTTP body itself.

P5's `POST` approve, edit, and reject routes translate the engineer’s click into §1.4. They do not reimplement the hash check.

### 2.8 What evaluation supplies, without a Python call

The harness owns the fault manifest and the case id. It does not pass the true root cause into the graph. The graph sees the same alert a firing rule would send.

After the run, the harness scores the `RunRecord` the service wrote. `complexity` on that record comes from the manifest (`len(root_cause_services)`), which the harness knows and the graph does not.

## 3. Topologies share the inputs

C0, C1, and C2 receive the same tool registry, the same store, the same model ids, and the same `tool_budget`. Only the compiled graph changes.

| Topology | Decisions the model makes | Tools in the model's hands |
|---|---|---|
| `c0_workflow` | none | none. Code follows a fixed sequence. |
| `c1_single` | the single ReAct loop | every read tool and the proposal schemas |
| `c2_team` | commander routing, then each specialist | the per-agent lists in §2.4 |

C1 and C2 share the plan, the monitor, and the approval node. The monitor is not team-only. A comparison that gives the team a private verifier is not an E1 result.

`monitor` reads `last` and the plan and picks `retry`, `replan`, `stop`, or `ready`. The compiled solo graph does not include that node yet. After a failed verification it calls `apply_replan`, then returns to `solo` or stops. Retry, tool failure, and signature A are tested on `build_monitor_graph()` and are not on the solo edges. The solo order is `docs/oncall-graph-solo.md`.

Diagnosis fan-out, on C2 only: triage returns the suspect list first. The graph then runs one diagnosis copy per suspect. Each copy appends its statements to `hypotheses` and one §1.9 envelope to `diagnosis_reports`. Each copy's budget is `tool_budget // n`, remainder on the first copy. `parallel_diagnosis=false` is a config flag that gives one diagnosis agent the whole list and the whole budget.

## 4. Where the current code has not met this contract

The C1 baseline compiles and its tests pass. Callers should still build to the tables above. These differences are open:

| Contract | Current `oncall_graph` code |
|---|---|
| `scope` and `severity` are set by triage | The fields exist on the channel. C1 has no triage node, so they stay unset. |
| `diagnosis_reports` is filled by each diagnosis copy | The type is `{service, local_cause, tool_calls}`. Solo never writes the list. |
| Verification `signal` names the live check | The signal is `checkout_error_rate`. Recovery is still the scripted rate under `0.05`, not the live PromQL in the architecture notes. |
| The monitor's four edges sit on the shared gate | C1 calls `apply_replan` only. Retry, tool failure, and signature A run on `build_monitor_graph()`. |
| `tools` is a `ToolRegistry` | `None` or `{}` selects the scripted shop. Any other dict raises `NotImplementedError`. |
| `store` is searched for lessons | The argument is accepted and not read. |
| Model ids arrive from the service | `oncall_graph.config` holds a local pin. |
| Durable resume | The default saver is in-memory. Resume works in the same process. |
| `c0_workflow`, `c2_team` | `NotImplementedError`. |
| `RunRecord.usd` and `playbook_hash` | Not produced yet. |

`last.result` still holds the tool payload for the current turn, including a scripted log line. That payload is private. The evidence field other packages read is the reference list.

`oncall_graph.state.IncidentChannel` is the LangGraph channel. `oncall_contracts.state.IncidentState` is the type every other package imports.
