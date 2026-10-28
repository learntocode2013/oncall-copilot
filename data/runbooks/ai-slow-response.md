---
kind: cause
fault_id: ai-slow-response
status: hypothesis
service: agent
symptom: the demo AI agent responds slowly
severity: sev3
fix:
  action: set_flag
  args:
    flag: "aiSlowResponse"
    variant: "off"
rollback:
  action: set_flag
  args:
    flag: "aiSlowResponse"
    variant: "10sec"
preconditions:
  - the stack includes compose.agent.yaml — the agent service is NOT deployed by default
  - the flagd aiSlowResponse flag is set to 5sec or 10sec
  - agent latency matches the configured delay; no errors
queries:
  - the chatbot is slow to respond
  - agent responses take ten seconds
  - llm latency is high
  - assistant feels sluggish but works
---

# Slow agent responses

LLM responses in the demo's agent service are delayed by a fixed amount.

## Requires an overlay we do not currently deploy

Same as `ai-runaway-agent.md`: `agent`, `mcp` and `chatbot` live in `compose.agent.yaml`, which
our `COMPOSE` list omits. Unavailable until added.

## Variant class: duration

`off | 5sec | 10sec`. The variant is the delay, so confirm by comparing agent span duration
against it. Latency-only, errors zero — the fourth latency-only fault in the set, alongside
`image-slow-load`, `intl-shipping-slowdown` and `product-catalog-lock-contention`.

## Why it is worth having anyway

It is the only fault that degrades an **LLM-backed** service, which makes it the closest
analogue to our own system misbehaving. The diagnosis is uninteresting; the value is in asking
whether our dashboards would show it. Our Grafana panels track agent time-to-diagnose and cost
per incident — a 10-second model delay in a *monitored* agent is a useful sanity check that
those panels measure what we think they do.

## Fix

```
set_flag(flag=aiSlowResponse, variant="off")
```

## Severity

**sev3 PROPOSED** — slow, not broken, and off the purchase path entirely. *Confirm against the
severity policy.*

## Provenance

Variants and overlay placement read from the repo at tag `3.1.0`. **Behaviour not observed** —
the service is not deployed.
