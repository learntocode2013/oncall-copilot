---
kind: cause
fault_id: payment-unreachable
service: payment
symptom: checkout fails and the payment service is unavailable
fix: set_flag(flag=paymentUnreachable, variant="off")
rollback: set_flag(flag=paymentUnreachable, variant="on")
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

> **STATUS: DIRECTION OBSERVED, NOT YET CONCLUSIVE.** The fault has been injected once and the
> predicted discriminator appeared — but the sample was too small and the control was not
> healthy. **The agent must not cite this document as evidence.** See *What we measured*.

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

One run, three phases, 110-second trace windows over `service=checkout`:

| phase | payment spans | erroring | checkout error spans | payment success/s |
|---|---|---|---|---|
| baseline | 0 | 0 | 6 | 0.0000 |
| `paymentFailure` at `100%` | **6** | **4** | 4 | 0.0000 |
| `paymentUnreachable` on | **0** | 0 | **10** | 0.0000 |

The direction matches the prediction: when payment *fails*, payment spans exist and carry
errors; when payment is *unreachable* it never runs, so there are no payment spans and the
failure is recorded by the caller.

Error markers actually present on erroring spans — use these, not guesses:
`otel.status_code`, `error`, `rpc.response.status_code`, `otel.status_description`,
`error.type`.

**Why this is not yet conclusive — three problems:**

1. **Baseline also shows zero payment spans.** So absence alone does not separate
   `paymentUnreachable` from a quiet system. The discriminator must be the *conjunction*:
   checkout traffic present, errors on checkout spans, and no payment spans.
2. **Payment success rate was `0.0000` in every phase, baseline included.** There was no
   healthy control: no checkout reached payment even before the fault.
3. **Two to five checkout traces per phase.** Far too small to conclude.

**To finish it:** raise the checkout rate — longer windows (10+ minutes per phase) or
`loadGeneratorFloodHomepage` — establish a genuinely healthy baseline first, then re-measure.

**Open discrepancy:** the frontend 422 rate never appeared in any phase, including
`paymentFailure` at `100%`, despite having been measured at 0.05/s on a long-running stack.
See `payment-failure.md`.

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
