---
kind: cause
fault_id: kafka-queue-problems
status: structural
service: kafka
symptom: order events back up; accounting and fraud detection fall behind
severity: sev2
fix:
  action: set_flag
  args:
    flag: "kafkaQueueProblems"
    variant: "off"
rollback:
  action: set_flag
  args:
    flag: "kafkaQueueProblems"
    variant: "on"
preconditions:
  - the stack is running in MODE=full — kafka does not exist in minimal mode
  - the flagd kafkaQueueProblems flag is on
  - checkout still completes; the backlog is downstream
queries:
  - orders are completing but accounting is behind
  - kafka queue is backing up
  - consumer lag is growing
  - fraud detection is not processing orders
---

# Kafka queue problems

Kafka is overloaded while a consumer-side problem is introduced simultaneously, so order events
accumulate.

## This fault may not exist on your stack

`kafka` lives only in `compose.full.yaml`. **In `MODE=minimal` the service is absent and this
fault family is unavailable** — along with `accounting` and `fraud-detection` as diagnosable
services.

That has two consequences:

- **For diagnosis:** the agent must not propose a kafka hypothesis on a stack where kafka is
  not deployed. The deployed service set is part of the evidence.
- **For evaluation:** eval runs must use `MODE=full`, or this fault type is silently missing
  from the suite. `make preflight` prints the current mode for exactly this reason.

## The damage is asynchronous

`kafka` has **no dependants** in compose, so nothing is blocked from starting and checkout keeps
completing orders. The failure is invisible on the purchase path and visible only downstream:
`accounting` and `fraud-detection` consume from the `orders` topic and fall behind.

This makes it a good test of whether the agent reasons about **consumers rather than
requests**. An error-rate or latency view of the shop shows nothing wrong.

## Detection

Consumer lag rather than error rate. The `otel-collector` is patched by `compose.full.yaml` to
add a Kafka metrics receiver, so kafka metrics should be present in Prometheus when running in
full mode — confirm which series actually appear before writing a query, as we learned the hard
way with the 422 series.

## Fix

```
set_flag(flag=kafkaQueueProblems, variant="off")
```

Variant class: **boolean** (`off | on`).

## Severity

**sev2 PROPOSED** — customers are unaffected, but order records are incomplete, which is a
business-data problem rather than an availability one. *Confirm against the severity policy.*

## Provenance

Variants, 620 MiB cap, absence of dependants and full-mode-only placement read from the repo at
tag `3.1.0`. **Behaviour not yet observed**, and the available kafka metrics are unconfirmed.
