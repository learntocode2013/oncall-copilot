---
kind: cause
fault_id: payment-failure
service: payment
symptom: checkout returns HTTP 422 and payment transactions stop
fix: set_flag(flag=paymentFailure, value=false)
rollback: set_flag(flag=paymentFailure, value=true)
severity: sev1
preconditions:
  - the flagd paymentFailure flag is enabled
  - payment spans are present in traces and carrying errors (if absent, suspect paymentUnreachable)
  - no deployment of the payment service in the last 30 minutes
queries:
  - checkout is returning 422 and orders are not completing
  - payment transactions dropped to zero
  - customers cannot complete checkout, payment service erroring
---

# Payment charge failures

The payment service rejects a percentage of charge requests. Checkout cannot complete, so
the frontend returns **HTTP 422** and successful payment transactions stop.

## Detection

**There is no payment error counter.** `demo_payment_transactions_total` counts only
*successes*, broken down by currency. The fault therefore shows up as a **collapse in
successes** alongside 422s at the frontend — never as a rising error metric. Any alert rule
has to be built from those two facts.

Two expressions that look correct and detect nothing, both verified against a live stack:

1. **Filtering on `http_route`** returns *no data*. The frontend's HTTP series carry no
   `http_route` label at all — only `jaeger`, `cart`, `shipping` and `ad` populate it. A rule
   scoped to `http_route="/api/checkout"` sits green through a total outage.
2. **Using all frontend traffic as the denominator** measures about **0.5%** during a complete
   payment outage, because the load generator browses far more often than it checks out. A
   `> 5%` threshold never fires.

The working expression pairs 422s with payment successes. It reads `1.0` with the fault on, and
on an idle shop both terms are zero, so the result is `NaN` and it cannot fire spuriously:

```promql
sum(rate(http_server_request_duration_seconds_count{
      service_name="frontend", http_response_status_code="422"}[5m]))
/ ( sum(rate(http_server_request_duration_seconds_count{
        service_name="frontend", http_response_status_code="422"}[5m]))
    + sum(rate(demo_payment_transactions_total[5m])) )
```

## Localisation

**Metrics establish that checkout is broken. They do not say why.**

`checkout`'s `rpc_client` series carry no `rpc_grpc_status_code` label, so gRPC-level failures
are invisible in metrics entirely. Attribution to `payment` requires **traces**: look for
checkout spans whose child payment spans carry error status.

This is the discriminator against `paymentUnreachable`, where payment spans are expected to be
**absent** rather than erroring. See `checkout-error-rate-elevated` for the full candidate
table — and note that row is still marked unverified.

## Fix

```
set_flag(flag=paymentFailure, value=false)
```

**Expected effect:** `rate(demo_payment_transactions_total[5m])` returns above zero within
about two minutes, and the 422 rate falls to zero.

**Rollback:** `set_flag(flag=paymentFailure, value=true)`.

**Verification is not optional.** Confirm recovery in the success counter, not in the absence
of errors — the absence of 422s is also what an idle shop looks like.
