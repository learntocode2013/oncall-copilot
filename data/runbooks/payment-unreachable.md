---
kind: cause
fault_id: payment-unreachable
status: verified
service: payment
symptom: checkout fails and the payment service is unavailable
severity: sev1
fix:
  action: set_flag
  args:
    flag: "paymentUnreachable"
    variant: "off"
rollback:
  action: set_flag
  args:
    flag: "paymentUnreachable"
    variant: "on"
preconditions:
  - the flagd paymentUnreachable flag is enabled
  - payment spans are ABSENT from checkout traces (if present but erroring, suspect payment-failure)
  - no deployment of the payment service in the last 30 minutes
queries:
  - payment is down and checkout is failing
  - checkout cannot reach payment
  - no payment spans in traces at all
  - connection refused calling payment from checkout
---

# Payment service unreachable

The payment service is unavailable, so checkout cannot place a charge call at all.

> **STATUS: VERIFIED** against a healthy baseline at 25 concurrent users. Two independent
> discriminators confirmed — one in metrics, one in traces. See *What we measured*.

## The distinction that matters

`payment-failure` and `payment-unreachable` produce the same user-facing symptom — checkout
returns HTTP 422 and `demo_payment_transactions_total` flatlines. Telling them apart is the
whole diagnostic task, and getting it wrong means proposing the wrong flag.

| | `payment-failure` | `payment-unreachable` |
|---|---|---|
| What the flag does | fails *n%* of charge requests | payment is unavailable |
| Payment spans in traces | **present**, carrying error status | **expected absent** |
| Failure shape | a proportion of checkouts | expected: all checkouts |
| Expected error | application-level charge rejection | expected: connection-level failure |
| Verified? | **yes** | **no — hypothesis** |

Metrics cannot separate these. `checkout`'s `rpc_client` series carry no
`rpc_grpc_status_code` label, so a connection failure and an application error look the same
in metrics — both simply mean fewer successes. **Only traces can discriminate**, by whether a
payment span exists at all.

## What we measured

Three phases at 25 concurrent users, with a genuinely healthy control:

| phase | checkouts | failures | frontend status codes |
|---|---|---|---|
| baseline | 26 | **0** | none |
| `paymentFailure` at `100%` | 37 | 37 | **422 only** (+21) |
| `paymentUnreachable` on | 28 | 28 | **422 (+13) and 500 (+16)** |

### Discriminator 1 — metrics

**`paymentFailure` produces 422 only. `paymentUnreachable` produces 422 *and* 500.**

The presence of frontend `500`s alongside `422`s separates the two without touching traces.
This was not predicted; the original hypothesis was that unreachable would produce 500s
*instead of* 422s, and it produces both.

```promql
sum(rate(http_server_request_duration_seconds_count{
      service_name="frontend", http_response_status_code="500"}[5m])) > 0
```

### Discriminator 2 — traces

From an earlier run: when payment *fails*, payment spans are **present and carrying errors**;
when payment is *unreachable* it never runs, so there are **no payment spans** and the failure
is recorded on the caller's span instead.

Error markers actually present on erroring spans — use these, not guesses:
`otel.status_code`, `error`, `rpc.response.status_code`, `otel.status_description`,
`error.type`.

Note the trace discriminator needs a conjunction, not absence alone: *checkout traffic present,
errors on checkout spans, and no payment spans*. A quiet system also has no payment spans.

### Caveats

Failure counts and status-code deltas do not reconcile exactly (37 failures against +21 × 422),
because the locust counters and the Prometheus windows are sampled at slightly different
moments. Treat the proportions as sound and the absolute counts as approximate.

## Fix

```
set_flag(flag=paymentUnreachable, variant="off")
```

**Expected effect:** `rate(demo_payment_transactions_total[5m])` returns above zero within
about two minutes.

**Rollback:** `set_flag(flag=paymentUnreachable, variant="on")`.

`paymentUnreachable` is a **boolean** flag (`off` / `on`), unlike its sibling
`paymentFailure` which takes percentages. Do not assume `on` exists on any flag.

**Verify in the success counter, not in the absence of errors** — an idle shop also has no
errors.

## Why this pair matters beyond this incident

Signature challenge A requires a trace in which the first hypothesis is wrong and the agent
recovers. This pair is how you engineer it honestly: inject one, and see whether the agent
commits to the other before the trace evidence arrives. That is a real diagnostic failure
mode, not a staged one.
