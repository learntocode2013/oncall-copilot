---
kind: symptom
status: structural
service: checkout
symptom: checkout error rate elevated, orders not completing
severity: sev1
candidates:
  - payment-failure
  - payment-unreachable
  - cart-failure
  - product-catalog-failure
  - load-generator-flood-homepage
queries:
  - checkout error rate above 5 percent
  - orders are failing at the last step
  - why is checkout broken
  - error rate alert fired on checkout, where do I start
---

# Checkout error rate elevated

Entry point for any alert reporting failed or stalled checkouts. This document does **not**
contain a fix — its job is to narrow to one cause, which then has its own runbook.

## First: is anything actually broken?

A spike in checkout traffic is not a fault. Before diagnosing, check whether request *volume*
rose at the same time as the error count. If both moved together and the error *share* is flat,
suspect `load-generator-flood-homepage` — a load spike with no underlying fault — and do not
propose a remediation.

## Candidate causes and what separates them

| Candidate | Discriminating evidence | Status |
|---|---|---|
| `payment-failure` | payment spans **present**, carrying error status; `demo_payment_transactions_total` flat at zero; frontend 422s | **verified** |
| `payment-unreachable` | payment spans **absent**; checkout→payment connection failures in traces | **UNVERIFIED — hypothesis** |
| `cart-failure` | cart gRPC errors upstream of checkout; failures affect a share of sessions | **UNVERIFIED** |
| `product-catalog-failure` | errors scoped to specific products; catalog spans erroring | **UNVERIFIED** |
| `load-generator-flood-homepage` | error *count* rises but error *share* is flat; request volume up | **UNVERIFIED** |

> **Only the first row has been observed.** The others are reasoned from the flag descriptions
> in `src/flagd/demo.flagd.json` and must be confirmed by injecting each fault and recording
> what Prometheus and Jaeger actually show. Until then, treat them as a search order, not as
> facts, and do not let the agent cite this table as evidence.

## Why metrics alone are not enough here

The frontend's HTTP series carry no `http_route` label, and `checkout`'s `rpc_client` series
carry no `rpc_grpc_status_code`. So metrics can tell you *checkout is failing* and cannot tell
you *which downstream service caused it*. Every row in the table above is separated by trace
evidence, not metric evidence.

This is the strongest argument for the `telemetry` tool returning correlated metric **and**
trace results rather than metrics alone.

## Severity

A total checkout outage is **sev1**: revenue-affecting, all users, no workaround.

A partial failure affecting a percentage of sessions is sev1 while the error share is above the
alert threshold, because customers cannot complete purchases and retrying may not help.
