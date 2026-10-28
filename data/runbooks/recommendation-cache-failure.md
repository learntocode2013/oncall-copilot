---
kind: cause
fault_id: recommendation-cache-failure
status: structural
service: recommendation
symptom: recommendation service degrades as its cache fails
severity: sev3
fix:
  action: set_flag
  args:
    flag: "recommendationCacheFailure"
    variant: "off"
rollback:
  action: set_flag
  args:
    flag: "recommendationCacheFailure"
    variant: "on"
preconditions:
  - the flagd recommendationCacheFailure flag is on
  - the recommendation health-check override from ADR-0001 is in place
  - recommendation memory is below its 500 MiB cap
queries:
  - recommendations are slow or missing
  - recommendation cache is not working
  - recommendation service degraded
  - product recommendations stopped appearing
---

# Recommendation cache failure

The recommendation service's cache fails, so it recomputes or refetches on every request.

## Read this before diagnosing: a patched health check

**This fault targets the one service whose health check we had to patch.** At tag `3.1.0`,
`recommendation`'s health check is `nc -z localhost 9001`, and `nc` is not in the image — so it
can **never** pass. `frontend` requires `recommendation: service_healthy`, which means the
entire shop was unreachable until we overrode it. See
`docs/decisions/0001-patch-recommendation-healthcheck.md`.

Consequences for diagnosis:

- **With our override in place**, container health is a real signal here.
- **Without it** — a teammate on an unpatched checkout, or after a submodule bump that drops
  the override — "recommendation unhealthy" says nothing about the cache at all. It is the
  broken probe, and no flag will fix it.

Check the override is present before trusting the health state. This is a precondition, not a
footnote.

## Blast radius

`frontend` requires `recommendation` to be **`service_healthy`**, so a stack restart while
anything is wrong with this service leaves port 8080 unbound.

## Detection

Expect elevated latency on recommendation spans and increased calls from `recommendation` to
`product-catalog`, since a failed cache means refetching. **Latency without errors** is the
likely signature, which error-rate alerts will not catch — see
`product-catalog-lock-contention.md` for the same trap.

## Fix

```
set_flag(flag=recommendationCacheFailure, variant="off")
```

Variant class: **boolean** (`off | on`).

## Severity

**sev3 PROPOSED** — recommendations are off the purchase path. *Confirm against the severity
policy.*

## Provenance

Variants, 500 MiB cap and the dependency condition read from the repo at tag `3.1.0`. The
broken health check and its override are **measured** (ADR-0001). **This flag's own behaviour
is not yet observed** — in particular the "latency without errors" claim and the extra
product-catalog traffic are reasoned, not seen.
