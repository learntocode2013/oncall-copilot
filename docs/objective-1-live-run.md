# Objective 1, run live: what the system reported and what the shop actually was

**Captured by P1 on 2026-11-07 against `main` at `7884566` with PR #27's telemetry image
deployed.** Mode `hybrid-live`, topology `c1_single`, tool budget 10, model
`openai/gpt-4o-mini`. For P2 and P4; this is the evidence behind
[`demo-objectives-gap-analysis.md`](demo-objectives-gap-analysis.md), which was read out of the
code. This one was observed.

The objective, from the project file:

> Turn on `paymentFailure`. Show the agent finding payment errors in the traces and proposing to
> switch the flag off, followed by the engineer's approval and verified recovery.

That is five beats. Here is where each one stands after a live run.

| Beat | Observed | |
| --- | --- | --- |
| 1. Turn on `paymentFailure` | the lab writes `deploy/flagd/demo.flagd.json`, flagd picks it up | **works** |
| 2. finding payment errors in the traces | the tool returns them; the graph's evidence layer drops them | **half** |
| 3. proposing to switch the flag off | proposed a flag that does not exist, twice out of two | **does not work** |
| 4. the engineer's approval | gate, `payload_hash`, 409 on a stale hash | **works** |
| 5. verified recovery | reports `RECOVERED` with the fault still armed in the real shop | **fiction** |

Beat 3 is a finding that was not in the gap analysis. Beats 2 and 5 are confirmed as written
there, with run output rather than citations.

---

## 1. The live page, as it happened

Two distinct investigations ran. Both reached the approval gate. Neither proposed the correct
action.

```text
run A  incident alert-e2c7910f486d
       proposed: set_flag {"flag": "checkout_error_handling", "variant": "0.8"}
run B  incident alert-c982ca227a2d
       proposed: set_flag {"flag": "checkout_error_rate", "variant": "high"}
```

The correct action, which the lab publishes as `correct_action`, is
`set_flag {"flag": "paymentFailure", "variant": "off"}`. Neither proposal names
`paymentFailure`. Run B's proposal is backwards on its face: it asks to set the error rate
*high*.

**The model was handed the right answer and did not use it.** In a live run `search_runbooks` is
inherited from the fixture, and it returns two passages whose `fix` field reads, verbatim:

```text
set_flag paymentFailure variant off
```

The transcript shows the model calling `search_runbooks({'symptom': 'checkout error rate above
5%'})`, receiving those passages, and then proposing a flag it invented. Run A's full tool
sequence:

```text
solo calls promql({'query': 'rate(checkout_errors_total[5m])'})
solo calls log_search({'service': 'checkout', 'query': 'error'})
solo calls traces({'service': 'checkout'})
solo calls search_runbooks({'symptom': 'checkout error rate above 5%'})
solo calls set_flag({'flag': 'checkout_error_handling', 'variant': '0.8',
                     'expected_effect': 'reduce checkout error rate'})
```

Note what happens next, because it is not an error state. The approval was granted, `apply` ran,
verification failed, and the graph **re-proposed and went back to `AWAITING_APPROVAL`**:

```text
Alert accepted; investigation queued
Agent investigation started
One structured action needs review      <- propose
Approved command resumed                <- the engineer approves
One structured action needs review      <- propose again, same invented flag
```

So a live run on a wrong proposal does not fail. It loops on the approval gate, and the incident
never reaches a terminal state. `verification` after the first approval reads
`{"recovered": false, "signal": "checkout_error_rate"}`.

**`set_flag` accepts any flag name.** `stubs.py:197` is `self.flags[clean["flag"]] = clean[
"variant"]` — a bare dict assignment, so an invented flag is written as happily as a real one and
`apply` returns `"changed": true`. Nothing in the write path knows which flags exist.

**One more thing worth knowing before you demo:** paging the same fault three times in a minute
gives you **one** incident. `IncidentStore.reserve_alert` keys on fingerprint, or alertname plus
`startsAt`, and returns the investigation already open for that key. Runs 2 and 3 of my three
pages returned run B's id and started nothing.

---

## 2. The recovery fiction, deterministically

Beat 3 failing means the live page above never reached beat 5, so I demonstrated beat 5 without
the model. This drives the two graph nodes directly, with the exact shop object `main.py:272`
builds for a live incident and the exact action the lab publishes as correct. It is deterministic
and says the same thing every time.

```python
import json
from pathlib import Path

from oncall_graph.astronomy_shop import LiveShop
from oncall_graph.nodes.remediate import RECOVERY_LINE, verify_metrics

FLAG_FILE = Path("deploy/flagd/demo.flagd.json")


def flag_on_disk():
    return json.loads(FLAG_FILE.read_text())["flags"]["paymentFailure"]["defaultVariant"]


shop = LiveShop(host="localhost")
action = {"action": "set_flag", "args": {"flag": "paymentFailure", "variant": "off"}}

print("before:", flag_on_disk(), shop.metrics())
print("apply :", shop.apply(action["action"], action["args"]))
print("after :", flag_on_disk(), shop.metrics())
print("verify:", verify_metrics({}, shop)["status"], "line", RECOVERY_LINE)
```

Output, with `paymentFailure` armed for real:

```text
the shop a live incident actually gets: LiveShop
  overrides: ['log_search', 'promql', 'traces']

1. the real flag, before: '100%'
   shop.armed('paymentFailure') = True
   shop.metrics() = {'error_rate': 0.08, 'service': 'checkout'}

2. the engineer approves the CORRECT action:
   {"action": "set_flag", "args": {"flag": "paymentFailure", "variant": "off"}}
   shop.apply returned: {"ok": true, "tool": "set_flag", "flag": "paymentFailure",
                         "variant": "off", "previous": "100%", "changed": true}
   shop.flags is now:   {"paymentFailure": "off"}   <- an in-memory dict

3. the real flag, after the approved action ran: '100%'

4. the graph's own verification node, reading the same shop:
   shop.metrics() = {'error_rate': 0.01, 'service': 'checkout'}  (recovery line 0.05)
   verification = {"recovered": true, "signal": "checkout_error_rate"}
   STATUS       = RECOVERED

So: status RECOVERED with paymentFailure = '100%' in the real shop.
```

The mechanism, in three lines of other people's code:

1. `astronomy_shop.py:48` — `LiveShop` overrides `promql`, `log_search` and `traces`. **Three of
   ten.** `apply`, `armed` and `metrics` are inherited from the scripted fixture. (The gap
   analysis said "three of nine"; counted off the class, `Shop` has ten public methods. Corrected
   there in this PR too.)
2. `remediate.py:165` — `result = shop.apply(action["action"], action["args"])`. That mutates
   `self.flags`, an in-memory dict. It opens no file and makes no network call.
3. `remediate.py:181` — `verify_metrics` reads `shop.metrics()`, which is
   `0.08 if self.armed() else 0.01`, and `armed()` reads `self.flags`.

So the verification reads back the dict the action just wrote. The docstring on that node says
"The model does not decide" and it is true: the model does not decide, the agent's own write
does. `apply` reports `"changed": true` and it did change something — just not the shop.

Three ways to get a green run out of this today: approve the correct action (above), approve
nothing and arm nothing (a page on a healthy shop still "recovers", because `0.01 < 0.05`), or
approve a wrong action and watch it loop instead of fail.

---

## 3. What each of us owns here

**P2 — the adapter seam (gap analysis item 1a).** Beats 3 and 5 both land here.

- `apply` has to reach `mcp-ops-actions`, which already writes the flagd file and already returns
  the `previous` variant for the undo. The seam is `main.py:272`: a live incident gets
  `Solo(shop=LiveShop())` and never touches `build_graph` or any MCP server.
- `set_flag` should refuse a flag that does not exist. Both live proposals would have been
  rejected at the tool boundary by a check against the flagd config, and an invented flag reaching
  `apply` is how a wrong action becomes a successful write.
- The proposal quality is a model question, not only a code one. `gpt-4o-mini` had
  `set_flag paymentFailure variant off` in its context from `search_runbooks` and proposed
  something else, twice. Worth trying a stronger model before concluding anything about the
  prompt — but **n = 2**, so this is an observation, not a measured rate.
- The approval loop on a failed verification needs a bound. Right now a wrong action can be
  approved indefinitely and the incident never terminates.

**P4 — what "verified" means (gap analysis item 8).** The fault's own `detect` query is

```text
sum(rate(http_server_request_duration_seconds_count{service_name="frontend",
    http_response_status_code="422"}[5m]))
  / (that + sum(rate(demo_payment_transactions_total[5m])))   >= 0.5
```

That is a five-minute rate, so a truthful verification cannot be instant, and on a young stack it
returns no series at all rather than zero. The current `verify_metrics` compares a fixture number
to `RECOVERY_LINE = 0.05`, a threshold with no relationship to that `0.5`. Whatever replaces it
has to distinguish *recovered*, *not yet recovered* and *cannot tell yet* — and "applied, not yet
verified" is a better demo than a `RECOVERED` that means nothing.

**P1 — mine, and done.** Beat 2's tool half: `traces` returns the errored spans as of #27.
Beat 2's other half is P2's evidence extraction — `handoffs.py:133` keys a trace finding off
`result.get("local_cause") is True`, and the real `traces` does not return `local_cause`, because
a verdict is not a read. The rows reach the model's context and are ignored by the deterministic
triage layer.

---

## 4. Reproducing this

```bash
make -C deploy up                                       # 33 containers, verify green
curl -sX POST localhost:8020/api/faults/payment-failure/arm
python3 -c "import json;print(json.load(open('deploy/flagd/demo.flagd.json'))\
['flags']['paymentFailure']['defaultVariant'])"         # -> 100%
curl -sX POST localhost:8020/api/faults/payment-failure/page \
     -H 'Content-Type: application/json' -d '{"tool_budget":10}'
curl -s localhost:8000/incidents/<id> | jq '.status, .approval.command'
curl -sX POST localhost:8000/incidents/<id>/approve \
     -H 'Content-Type: application/json' \
     -d '{"decision":"approve","payload_hash":"<hash from the snapshot>"}'
# then read the flag file again: still 100%
curl -sX POST localhost:8020/api/faults/payment-failure/disarm
```

**Disarm before running `make guards`.** Arming rewrites `deploy/flagd/demo.flagd.json`, and
`test_no_fault_is_enabled_in_the_committed_flag_baseline` refuses a fault committed as enabled —
it caught me twice while capturing this.
