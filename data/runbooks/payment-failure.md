---
kind: cause
fault_id: payment-failure
service: payment
symptom: checkout returns HTTP 422 and payment transactions stop
fix: set_flag(flag=paymentFailure, variant="off")
rollback: set_flag(flag=paymentFailure, variant="100%")
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

## Resolved: why the 422 series was once missing

On a freshly started stack the `http_response_status_code="422"` series did not exist at all,
even with `paymentFailure` at `100%`. **The cause was not the metric or the rule.**

`checkout` ships a 20 MiB cap with `GOMEMLIMIT=16MiB`. Under load its heap reaches the soft
limit and the Go runtime collects continuously — measured at **95.68% of cap and 268% CPU**.
Requests stall, **Envoy returns 504 because the frontend never responded**, and a 504 generated
by the proxy produces *no* `http_server_request_duration_seconds_count` sample at all. No
status code is emitted, so the 422 series never increments.

Locust confirmed the shape: `POST /api/checkout` failed **504 × 93**, 500 × 22, 422 × 3.

After raising checkout's ceiling (see `docs/decisions/0002-*`), the same experiment produced a
clean result — baseline 26 checkouts and **zero** failures, then 37 failures with **+21 × 422**.

**The lesson for diagnosis:** a failure severe enough to stall the request is *invisible to
status-code metrics*, because no status code is ever recorded. An error-rate alert that reads
zero can mean "healthy" or "too broken to answer". Distinguish them with request *volume* and
upstream 504s, not with the error ratio alone.

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
set_flag(flag=paymentFailure, variant="off")
```

**Expected effect:** `rate(demo_payment_transactions_total[5m])` returns above zero within
about two minutes, and the 422 rate falls to zero.

**Rollback:** `set_flag(flag=paymentFailure, variant="100%")`.

`paymentFailure` is a **percentage** flag — its variants are `off`, `10%`, `25%`, `50%`,
`75%`, `90%` and `100%`. There is no `on`. Setting an invalid variant does not error: the
flag simply never fires, and a run looks like a healthy system. Verified the hard way.

**Verification is not optional.** Confirm recovery in the success counter, not in the absence
of errors — the absence of 422s is also what an idle shop looks like.
