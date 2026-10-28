# Memory Policy

Owner: P1 — domain, knowledge & memory

Runbooks hold what the team knew before the incident. Memory holds what the copilot observed or
learned during one. This page governs the second.

## The rule everything else serves

**Memory must never silently promote a hypothesis into a fact.**

Every record carries a provenance status, using the same vocabulary as the corpus
(`data/runbooks/*.md` front matter):

| `status` | Means | The agent may |
|---|---|---|
| `observed` | read directly from telemetry during this incident | cite as evidence |
| `inferred` | the agent's conclusion from observations | cite **as a conclusion**, naming the observations |
| `approved` | a lesson a human reviewed and accepted | apply, if its preconditions match |
| `rejected` | reviewed and refused, kept with the reason | never apply; useful as a counterexample |

An output guardrail enforces this: a claim sourced from a record that is not `observed` must not
be presented as telemetry. This is the same standard the corpus uses — 16 of 18 runbooks are
`structural` or `hypothesis`, and the agent may not cite those as evidence either.

## Two stores, and we are not building a third

| | Where | Lifetime |
|---|---|---|
| **Short-term** — the live investigation | `IncidentState` in the LangGraph checkpointer | one incident |
| **Long-term** — episodes and lessons | the LangGraph store | across incidents |

`IncidentState` already carries `alert`, `scope`, `severity`, `hypotheses`, `evidence`, `plan`,
`proposed_action`, `verification` and `transcript`, with `operator.add` reducers on the
accumulating channels. **Working state does not go in the long-term store** — that would be a
second home for something already persisted, and the two would drift.

## What is saved

| Saved | Status | Why |
|---|---|---|
| Alert that opened the incident | `observed` | the incident's identity |
| Which hypotheses were tested and their outcome | `inferred` | stops the agent re-testing a dead end |
| **References** to evidence — trace IDs, the PromQL executed, the time window | `observed` | see *What is never saved* |
| The exact action approved, by whom, and the payload | `observed` | the audit trail the approval gate exists to produce |
| Verification result after the action | `observed` | whether the fix worked |
| Candidate lessons proposed by the Reflexion loop | `inferred` → `approved`/`rejected` | the playbook |

## What is never saved

- **Raw telemetry payloads.** Store the trace ID and the query, not the log line. Logs on this
  stack can contain PII — the demo ships an `emitRawPii` flag that emits `user.email`,
  `demo.payment.card_number` and `card_cvv` — so a memory store holding raw log text becomes a
  PII store with none of the controls. References are also smaller and stay valid.
- Secrets, credentials, tokens, API keys. Input guardrails redact before any model sees a log
  line; memory must not reintroduce what redaction removed.
- Customer identifiers of any kind. Nothing in diagnosis needs them.
- Anything the agent generated but a human has not seen, in a field that reads as fact.

## When it is saved

Writes happen at defined points, never opportunistically:

1. **On incident open** — the alert, as `observed`.
2. **After each hypothesis is tested** — outcome plus evidence references.
3. **After the approval gate resolves** — the exact approved payload and the approver. Never
   before: an approval that was edited is not the approval that was requested.
4. **After verification completes** — the measured outcome. An action is recorded as successful
   only when a success signal confirms it, never on the absence of errors. An idle shop also
   has no errors.
5. **After human review of a candidate lesson** — as `approved` or `rejected`.

## Who can read it

| | Access |
|---|---|
| All five owners | read and write, via the repo and the incident UI |
| The agent, during an incident | read `observed`, `inferred`, `approved`; **never** `rejected` as guidance |
| The evaluation harness | read-only, and **never** during a scored run (see *Evaluation boundary*) |
| Anyone else | no access; the store is local to the deployment |

There is no multi-tenancy and no external consumer. If that changes, this section changes first.

## How it is corrected or deleted

- **Correct a record:** append a superseding record referencing the original; never edit in
  place. The original stays with its status changed to `superseded`, because "we believed X at
  10:04" is itself evidence when reconstructing a timeline.
- **Reject a lesson:** set `rejected` with the reason. Keep it — the guide requires one rejected
  lesson and its reason as a deliverable, and a rejected lesson is a useful counterexample.
- **Delete an incident:** `forget(incident_id)` removes the episode **and every lesson derived
  from it**, by the `derived_from` field each lesson carries. A lesson that outlives the
  incident that justified it is unfalsifiable.
- **Delete everything:** the store is a file in `artifacts/`, which is gitignored and
  reproducible. Deleting it loses the playbook and nothing else.

## Lessons: the lifecycle

A lesson is not an observation. It is a generalisation the agent proposes, and it enters the
playbook only through a person.

```
Reflexion loop proposes          (status: inferred)
  -> human reviews               (approved | rejected, with a reason)
    -> enters the playbook       (approved only)
      -> PLAYBOOK FROZEN         before any held-out run
```

Every lesson states **preconditions**. Before applying one, the agent checks each precondition
against *current* telemetry. A lesson whose preconditions do not match is not applicable, and
saying so is a correct outcome — it is demo scene 3.

A worked example from this project's own history. After the first incident, a reasonable lesson
was *"when a service is unhealthy but up, check recent flag and config changes first."* That
lesson is correct for `docs/decisions/0001-*` — a broken health check. Applied to
`docs/decisions/0002-*`, a garbage-collection spiral against a memory ceiling, it sends you
entirely the wrong way: nothing had changed, and `RestartCount: 0`, `OOMKilled: false`,
`ExitCode: 0` all looked reassuring. **The precondition that separates them is "a configuration
or flag change precedes the alert."** Without it, the lesson is a trap.

## Evaluation boundary

**Memory is reset between evaluation cases.** Without that, a lesson learned on case 7 changes
case 8, and the held-out score measures the order the cases ran in.

- Development runs: memory on, lessons may be proposed.
- **Held-out runs: the playbook is frozen and no new lessons are written.**
- The required memory-on versus memory-off comparison uses the same frozen playbook for both
  arms, so the difference is the memory, not a moving target.

This is the same failure class as held-out contamination, and it is silent in the same way: it
makes scores go *up* and raises no error. It belongs in `tests/test_guards.py` alongside
`test_playbook_frozen_before_heldout`, not in a convention.

## What is code and what is judgement

Per the project's first rule — *the model proposes; code and people decide*:

**Code:** the write gates above; precondition evaluation against current telemetry; the
provenance status on every record; the per-case reset; `forget(incident_id)` cascading to
derived lessons.

**Judgement:** whether a candidate lesson generalises; whether two incidents are alike enough
for one to inform the other; what a precondition should say.

**A person, always:** approving a lesson into the playbook, and approving any action.
