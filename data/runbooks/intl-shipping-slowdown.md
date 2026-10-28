---
kind: cause
fault_id: intl-shipping-slowdown
status: structural
service: shipping
symptom: international shipping quotes are slow, checkout stalls
severity: sev2
fix:
  action: set_flag
  args:
    flag: "intlShippingSlowdown"
    variant: "off"
rollback:
  action: set_flag
  args:
    flag: "intlShippingSlowdown"
    variant: "10sec"
preconditions:
  - the flagd intlShippingSlowdown flag is set to 5sec or 10sec
  - shipping span duration matches the configured delay
  - checkout latency rises in step with shipping latency
queries:
  - checkout is slow but eventually works
  - shipping quotes take ten seconds
  - international orders are slow
  - checkout latency spiked with no errors
---

# International shipping slowdown

Shipping delays its responses by a fixed amount. Checkout calls shipping, so checkout inherits
the delay.

## Variant class: duration

`off | 5sec | 10sec`. The variant is the delay, so confirm by comparing shipping's span
duration against it.

## The reason this one is dangerous

A slow dependency on the checkout path can escalate into something far worse than slowness.
We measured this exact mechanism on this stack:

> When `checkout` stalls, **Envoy returns 504 because the frontend never responded** — and a
> proxy-generated 504 emits **no `http_server_request_duration_seconds_count` sample at all.**
> No status code is recorded, so error-rate metrics read **zero** while every order is failing.

See `payment-failure.md` → *Resolved: why the 422 series was once missing*, and
`docs/decisions/0002-*`.

So a 10-second shipping delay can present as:

1. elevated checkout latency, no errors — the honest symptom; or
2. **504s with no frontend status codes at all**, if the delay pushes checkout past Envoy's
   timeout.

In case 2 the error-rate alert reads zero and the shop is down. **Distinguish "healthy" from
"too broken to answer" using request volume and upstream 504s, never the error ratio alone.**

## Blast radius

`checkout` and `frontend` both depend on shipping with `service_started`, so nothing is blocked
at startup. Shipping has a 20 MiB cap but **no `GOMEMLIMIT`** — it is not one of the services
affected by the GC-spiral defect.

## Fix

```
set_flag(flag=intlShippingSlowdown, variant="off")
```

## Severity

**sev2 PROPOSED**, escalating to **sev1** if checkout begins returning 504s, because at that
point customers cannot purchase. *Confirm against the severity policy.*

## Provenance

Variants, 20 MiB cap, health check and dependency conditions read from the repo at tag `3.1.0`.
The 504-without-status-code mechanism is **measured** (ADR-0002). **This flag's own behaviour is
not yet observed** — whether 10s is enough to trigger the Envoy timeout is untested and worth
measuring, because it decides the severity.
