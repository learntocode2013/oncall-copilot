---
kind: cause
fault_id: ai-runaway-agent
status: hypothesis
service: agent
symptom: the demo AI agent loops tool calls until it hits its recursion limit
severity: sev2
fix:
  action: set_flag
  args:
    flag: "aiRunawayAgent"
    variant: "off"
rollback:
  action: set_flag
  args:
    flag: "aiRunawayAgent"
    variant: "on"
preconditions:
  - the stack includes compose.agent.yaml — the agent service is NOT deployed by default
  - the flagd aiRunawayAgent flag is on
  - tool-call count per request is climbing without the request completing
queries:
  - the agent keeps calling tools in a loop
  - runaway tool calling, cost is climbing
  - agent never finishes a request
  - recursion limit hit on the agent service
---

# Runaway agent loop

The demo's own AI agent is forced to loop tool calls until it hits its recursion limit.

## Requires an overlay we do not currently deploy

`agent`, `mcp` and `chatbot` live in `compose.agent.yaml` (500 MiB each), which our `COMPOSE`
file list omits. **This fault is unavailable until that overlay is added.**

Related: locust reports `POST /prompt` failing **288 times** with
`gaierror 'Name or service not known'` on every run, because the load-generator scenario
includes the agent flow unconditionally while the service only exists in that overlay. Adding
the overlay removes that permanent noise — which otherwise pollutes any aggregate error-rate
metric — at a cost of three services and likely LLM credentials for the demo's own agent.

## Why this fault matters more than the others

Our L14 hooks must include a **loop breaker**, and our pre-tool hooks enforce a per-incident
tool-call budget. This fault is a runaway tool-calling loop in a *different* agent — so it is
the closest available test of the thing our own hooks exist to prevent.

Two distinct questions it can answer:

1. **Can our agent diagnose a runaway loop in another system?** Tool-call count climbing with
   no completion, cost rising, no errors.
2. **Would our own loop breaker catch this shape?** Worth running our agent against this fault
   and separately asserting our budget hook fires on a synthetic loop.

The second is a red-team case for P3/P4 rather than a runbook item, but it is the more valuable
of the two.

## Detection

Tool-call count per request, not error rate. Expect cost and latency to rise with no errors
until the recursion limit trips. An error-rate view shows nothing.

## Fix

```
set_flag(flag=aiRunawayAgent, variant="off")
```

Variant class: **boolean** (`off | on`).

## Severity

**sev2 PROPOSED** — runaway cost with no customer-facing failure. *Confirm against the severity
policy.*

## Provenance

Variants and the overlay placement read from the repo at tag `3.1.0`. The `/prompt` failures are
**measured**. **Behaviour not observed** — the service is not deployed.
