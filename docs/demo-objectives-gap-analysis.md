# The three demo objectives, and what stands between us and them

**Written by P1 on 2026-11-07, against `600479e`. For P2 and P4.**

The project file asks the demo to show three things:

1. Turn on `paymentFailure`. Show the agent finding payment errors in the traces and proposing
   to switch the flag off, followed by the engineer's approval and verified recovery.
2. Show the log-borne "restart everything" instruction being blocked.
3. Show an old lesson being rejected because its preconditions do not match.

**None of the three can be demonstrated today**, and the first one fails in the way this project
keeps failing: it runs to completion and reports success. A live page right now reaches
`RECOVERED` without the real flag ever changing.

Everything below was read out of the code at `600479e`, not observed in a demo run. Every claim
names a file and a line so you can check it rather than take my word, and the last section lists
the commands.

---

## How a live incident runs today

`oncall-service` does not call `build_graph`. It constructs the solo topology directly:

```python
# packages/oncall-service/src/oncall_service/main.py:272
incident.solo = Solo(shop=LiveShop(), tool_budget=incident.tool_budget)
```

`LiveShop` overrides three of the shop's ten methods and inherits the rest from the
`paymentFailure` fixture:

| the agent calls | a live run actually reads | where |
| --- | --- | --- |
| `promql`, `log_search`, `traces` | **real** Prometheus, OpenSearch, Jaeger | `astronomy_shop.py:55-69` |
| `search_runbooks` | two hardcoded `paymentFailure` passages | `stubs.py:158` |
| `recent_changes`, `incident_record`, `status_draft` | fixture | `stubs.py` |
| `armed`, `metrics` | fixture, computed from an in-memory dict | `stubs.py:82,91` |
| `apply` — the only write | mutates `self.flags`; touches no file | `stubs.py:189` |

No MCP server is in that path. The three containers on 8101, 8102 and 8104 are reachable, healthy
and unused by an investigation.

---

## Objective 1: arm, diagnose, propose, approve, verify

Four of the five steps are real: the LLM, the tool-call loop and its budget, the proposal
contract, and the approval gate including its stale-hash refusal
(`main.py:625`, `409 approval payload hash is stale`). Two things are not.

### 1a. Recovery is self-certified — P2, with P4's ground truth

```python
# packages/oncall-graph/src/oncall_graph/nodes/remediate.py:181
def verify_metrics(state, shop):
    reading = shop.metrics()  # LiveShop does NOT override this
    rate = float(reading.get("error_rate") or 0)
    recovered = rate < RECOVERY_LINE  # 0.05
```

```python
# packages/oncall-graph/src/oncall_graph/stubs.py:91
def metrics(self) -> dict:
    rate = 0.08 if self.armed() else 0.01
```

```python
# packages/oncall-graph/src/oncall_graph/stubs.py:75
self.flags = {"paymentFailure": self.ARMED}
```

Follow it through. Every `LiveShop()` is born believing the fault is armed, whatever flagd says.
`metrics()` returns `0.08`. The agent proposes `set_flag paymentFailure off`. `apply` writes that
to a dict in memory. `metrics()` now returns `0.01`. `0.01 < 0.05`, so the run ends `RECOVERED`.

That loop is closed over the agent's own write and is independent of the system in both
directions:

- Arm nothing, and a live page still diagnoses `paymentFailure` and still "recovers".
- Arm the fault for real, and the run still reports `RECOVERED` while
  `deploy/flagd/demo.flagd.json` sits at `100%` and the shop is still broken.

The real telemetry reads feed the model's narrative. They do not feed the gate.

**What objective 1 needs:**

- `apply` reaches real flagd through `mcp-ops-actions` (8104), which already implements the exact
  `{flag, variant}` payload and already returns `previous` — see ADR-0004 and ADR-0005.
- `verify_metrics` reads the fault's real recovery signal instead of `shop.metrics()`.
  `eval/faults/manifest.yaml` already states it for `payment-failure`:
  `sum(rate(demo_payment_transactions_total[5m])) > 0`. **P4 owns that expression**; P2 owns
  calling it.
- One consequence worth agreeing on before coding: the real recovery query is a 5-minute rate,
  so a truthful verification cannot be instant. It either waits and polls or it reports
  "applied, not yet verified" — which is a better demo than a `RECOVERED` that means nothing, but
  it is a user-visible design change and it is P2's call.

### 1b. The traces tool cannot show payment errors — P1, mine

```python
# oncall_tools/servers/telemetry/client.py:273
def traces(self, service: str, search_depth: int = _MAX_TRACES) -> dict:
    """Recent trace ids for one service. Span bodies stay on Jaeger."""
```

and all it returns, twenty lines later at `:295`:

```python
return {
    "service": service,
    "count": len(found),
    "trace_ids": found,
    "window_minutes": self.minutes,
}
```

Ids and a count. No spans, no status codes, no error tags. The agent can learn that checkout has
three traces; it cannot learn that payment errored inside them. The payment evidence available
today comes from `log_search` and the 422 ratio in `promql`, which is a defensible diagnosis but
is not what the objective says.

"Span bodies stay on Jaeger" was my decision and it was a deliberate one — a tool that returns
whole traces returns a lot of attacker-controlled text into the model's context. It is now in
tension with a stated demo objective, so I am reopening it rather than defending it. **This is
mine to fix and it needs nothing from P2 or P4.** My intended shape: return per-trace error
status and the failing span's service and name, not whole span bodies.

> **DONE — PR #27.** `traces` now returns `errors`: one row per failing `(service, operation)`
> pair with a span count and up to three trace ids, plus `error_spans`, `error_traces`,
> `error_groups` and `spans_read`. Verified live against `jaeger:2.19.0` with `paymentFailure`
> armed: a query for **checkout** returns `payment | charge` and
> `payment | oteldemo.PaymentService/Charge` as errored, alongside checkout's own client span for
> the call into payment. Span attributes, `status.message`, events and stack traces are still not
> returned, so the attacker-controlled-text objection is answered rather than traded away.
>
> **Two things P2 should know before building interpretation on this.**
>
> 1. **The loudest error group is noise.** In that measured run Jaeger reported 28 errored spans
>    in 10 groups; 20 of them were routine `flagd`/`cart` flag evaluations, and the two `payment`
>    rows that are the entire point had **one span each** and sorted last. `errors[0]` is the most
>    frequent failing operation, not the cause. Anything that reads the top row will diagnose
>    flagd.
> 2. **The counts are not the row count.** `error_spans` and `error_groups` are totals over
>    everything found; `errors` is truncated at 24 rows and `error_groups_omitted` says by how
>    much. My first version summed the returned rows, which under-reported the errors in the
>    window in proportion to how many it had decided to print — caught by the live run, not by a
>    test, and now pinned by one.

---

## Objective 2: the log-borne instruction, blocked

This objective has **no demo material and no block.**

**No material.** No "restart everything" line exists anywhere — not in `data/`, not in the
stub logs (`stubs.py:263`), not seeded into OpenSearch. There is nothing for the agent to read,
so there is nothing for it to refuse. Someone has to author that line and get it in front of the
agent: either the lab publishes it, or it is seeded into the OpenSearch index the real
`log_search` reads. The data is P1's or P4's; see "what P1 will do" below.

**No block.** The defense today is prompt text in four places (`c1_single.py:38`,
`c2_team.py:70,75,84`) plus the telemetry server's own instructions. The planner does not stop it:

```python
# packages/oncall-graph/src/oncall_graph/planning/plan.py:27
ACTION_TOOLS = ("set_flag", "restart", "scale", "rollback")
```

`restart` is a **known** action, so `validate_action` accepts it (`plan.py:188`) and it travels to
the approval gate like any other proposal. Nothing refuses it in code.

Four lines below that constant sits the pattern that would work, already written and already
enforced for a different tool:

```python
# plan.py:30  DELIBERATELY NOT IN ACTION_TOOLS: a model may not propose a re-arm,
#             and validate_action rejects it as an unknown action.
ROLLBACK_FLAG_TOOL = "rollback_flag"
```

`restart`, `scale` and `rollback` have no schema in `ACTION_SCHEMAS`, no server behind them and no
corpus action that names them. CONTRIBUTING.md already records this as unreconciled and P2's call.
Objective 2 is the reason to settle it: if those three leave `ACTION_TOOLS` the way `rollback_flag`
did, an injected "restart everything" is refused by `validate_action` with a reason, and the demo
shows a mechanism instead of a model choosing to behave. **P2's call, and it is the only
version of this objective that survives a hostile question at the viva.**

---

## Objective 3: an old lesson rejected on preconditions

The machinery is written, and it is good: `precondition_matches` (`briefs.py:203`),
`partition_lessons` and `load_lessons` (`handoffs.py:273-308`). Three things stand between it and
the demo.

1. **It is wired only into `c2_team`** (`c2_team.py:465`). `c1_single` never calls `handoffs` at
   all — not in the topology, not in any node.
2. **The incident API refuses every other topology**:
   `raise HTTPException(status_code=422, detail="only c1_single is implemented")`
   (`main.py:561`). So nothing reachable over HTTP can run the code in point 1.
3. **There is no lesson store.** `load_lessons(None)` returns `([], [])` (`handoffs.py:300-301`),
   no `LessonStore` class exists in any package, and `data/postmortems/` is an empty directory —
   18 runbooks, zero postmortems.

As it stands this objective demonstrates an empty list being filtered into an empty list.

**Who does what:** the store and its seed content are P1's — memory is my component, and those
postmortems have been on my list since before this analysis. Points 1 and 2 are P2's: either
`c1_single` learns to filter retrieved passages and lessons the way `c2_team` does, or the
service accepts `c2_team` for the demo. I have a mild preference for the first, because the
objective is about *rejecting bad memory*, not about multi-agent orchestration, and demoing it on
the simpler topology removes a variable. But it changes P2's topology code, so it is P2's call.

---

## The prerequisite under all of this

CONTRIBUTING.md § "The tool registry, and the one thing it asks of `oncall-graph`" already asks
P2 for `agent._as_langchain_tool`'s third branch. That ask is now load-bearing for the demo, not
just for tidiness:

- My real runbook retrieval reaches the graph **only** through `mcp-runbooks`. Until the adapter
  exists, objective 1's diagnosis and objective 3's filtering both run against the two fixture
  passages in `stubs.py:158`, which name `paymentFailure` and cannot return anything else. A
  diagnosis citing them looks evidenced and cannot be wrong.
- The real write reaches flagd **only** through `mcp-ops-actions`. Until the adapter exists,
  objective 1's verification has nothing true to read.

So: adapter, then `build_graph`'s `NotImplementedError` lifts (`build.py:45-51`), then
`main.py:272` passes a registry instead of `LiveShop()`. Three changes, all P2's, and they gate
most of the rest.

---

## Ownership and a suggested order

| # | Work | Owner | Gated by |
| --- | --- | --- | --- |
| 1 | `_as_langchain_tool` third branch; lift `build_graph`'s refusal; service passes a registry | **P2** | — |
| 2 | `traces` returns error status and the failing span, not only ids | **P1** | — |
| 3 | Seed postmortems and a lesson store with one deliberately stale lesson | **P1** | — |
| 4 | Decide `restart`/`scale`/`rollback`: schema and server, or out of `ACTION_TOOLS` | **P2** | — |
| 5 | The injected log line, authored and reachable by `log_search` | **P1** | 4 (don't author data for a block that does not exist) |
| 6 | `apply` writes through `mcp-ops-actions`; `verify_metrics` reads the real recovery signal | **P2** | 1, and P4 for the expression |
| 7 | Precondition filtering reachable from the demo topology | **P2** | 3 |
| 8 | The recovery expression per fault, and what "verified" means when the signal is a 5m rate | **P4** | — |

Items 2, 3, 4 and 8 are independent and can start now. Item 1 unblocks 6 and 7.

## What P1 will do, so these asks are reciprocal

I am starting on item 3 — the lesson store and the seed postmortems — because objective 3 is
entirely blocked on me and authored content is the slowest part to get right. Item 2 follows.
Item 5 waits on P2's decision in item 4.

I will not touch `agent.py`, `build.py`, `plan.py`, `c1_single.py` or `main.py`. The viva asks
each owner to defend their own component, and a change I make in P2's package is one P2 cannot
defend.

## Verifying every claim above

```bash
# the live path builds the fixture shop by hand
grep -n 'LiveShop()' packages/oncall-service/src/oncall_service/main.py

# LiveShop overrides exactly three methods
grep -n 'def ' packages/oncall-graph/src/oncall_graph/astronomy_shop.py

# the gate reads the fixture, and the fixture starts armed
sed -n '181,186p' packages/oncall-graph/src/oncall_graph/nodes/remediate.py
sed -n '71,94p'  packages/oncall-graph/src/oncall_graph/stubs.py

# the write never leaves memory
sed -n '189,198p' packages/oncall-graph/src/oncall_graph/stubs.py

# traces returns ids only
sed -n '273,300p' packages/oncall-tools/src/oncall_tools/servers/telemetry/client.py

# restart is a known action, so validate_action accepts it
sed -n '27,38p'   packages/oncall-graph/src/oncall_graph/planning/plan.py
sed -n '181,200p' packages/oncall-graph/src/oncall_graph/planning/plan.py

# no injected log line exists
grep -rni 'restart everything' . --exclude-dir=.git --exclude-dir=vendor

# precondition filtering is c2_team only, and the API refuses c2_team
grep -rn 'load_lessons\|partition_passages' packages/oncall-graph/src/oncall_graph/
grep -n  'only c1_single is implemented' packages/oncall-service/src/oncall_service/main.py

# there is no lesson store and no postmortem
grep -rn 'class .*Store' packages/*/src
ls data/postmortems
```
