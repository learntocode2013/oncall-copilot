---
kind: cause
fault_id: payment-unreachable
service: payment
symptom: checkout fails and the payment service is unavailable
fix: set_flag(flag=paymentUnreachable, value=false)
rollback: set_flag(flag=paymentUnreachable, value=true)
severity: sev1
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

> **STATUS: HYPOTHESIS, NOT OBSERVATION.** This fault has not been injected. Everything below
> is reasoned from the flag description (*"Payment service is unavailable"*) and from the
> verified behaviour of its sibling `payment-failure`. **The agent must not cite this document
> as evidence.** It is a search order until the experiment in the Verification section is run.

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

## Verification needed

Run this before trusting any row above:

1. Inject `paymentFailure`. Record the frontend 422 rate, the payment success rate, and
   whether Jaeger shows payment spans — and if so, with what status.
2. `make reset` for a clean telemetry baseline, so the previous fault cannot bleed in.
3. Inject `paymentUnreachable`. Record the same three things.
4. Diff them. Whatever actually differs is the discriminator; write it here and delete this
   section.

Expect at least one surprise. The equivalent exercise on `payment-failure` produced two
plausible alert expressions that detect nothing at all.

## Fix

```
set_flag(flag=paymentUnreachable, value=false)
```

**Expected effect:** `rate(demo_payment_transactions_total[5m])` returns above zero within
about two minutes.

**Rollback:** `set_flag(flag=paymentUnreachable, value=true)`.

**Verify in the success counter, not in the absence of errors** — an idle shop also has no
errors.

## Why this pair matters beyond this incident

Signature challenge A requires a trace in which the first hypothesis is wrong and the agent
recovers. This pair is how you engineer it honestly: inject one, and see whether the agent
commits to the other before the trace evidence arrives. That is a real diagnostic failure
mode, not a staged one.
