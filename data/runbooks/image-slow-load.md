---
kind: cause
fault_id: image-slow-load
status: structural
service: image-provider
symptom: product images load slowly, pages render late
severity: sev3
fix:
  action: set_flag
  args:
    flag: "imageSlowLoad"
    variant: "off"
rollback:
  action: set_flag
  args:
    flag: "imageSlowLoad"
    variant: "10sec"
preconditions:
  - the flagd imageSlowLoad flag is set to 5sec or 10sec
  - image-provider latency matches the configured delay
  - no errors, only latency
queries:
  - product images take forever to load
  - the site feels slow but nothing is erroring
  - image provider is slow
  - pages render late with no errors
---

# Slow image loading

The image provider delays responses by a fixed amount. Pages still render correctly, late.

## Variant class: duration

`off | 5sec | 10sec`. The variant **is** the added delay, which makes this fault unusually easy
to confirm: measure image-provider's span duration and compare it against the variant. A
p95 near 5s or 10s with no error rate is close to conclusive.

This also makes it a good **negative** test for the diagnosis agents — latency is real, errors
are zero, and the correct conclusion is a specific flag rather than a service fault.

## Latency without errors

Error-rate alerting will read **zero** throughout. Any rule that only watches status codes
cannot see this fault at all. Compare `product-catalog-lock-contention.md` and
`recommendation-cache-failure.md` — three of the eighteen faults are latency-only, so a
diagnosis strategy built solely on error rates is blind to a sixth of the fault space.

## Blast radius

`frontend` requires `image-provider` to be **`service_healthy`**. Its health check is a
`wget --spider`, which has no timeout tight enough to fail on a 5–10s delay, so **the container
stays healthy while serving very slowly.**

## Fix

```
set_flag(flag=imageSlowLoad, variant="off")
```

## Severity

**sev3 PROPOSED** — the shop works; it feels bad. *Confirm against the severity policy.*

## Provenance

Variants, 120 MiB cap, health check and dependency condition read from the repo at tag `3.1.0`.
**Behaviour not yet observed.**
