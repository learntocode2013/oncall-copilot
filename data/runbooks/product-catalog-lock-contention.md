---
kind: cause
fault_id: product-catalog-lock-contention
service: product-catalog
symptom: product catalog latency rises sharply without hard errors
fix: set_flag(flag=productCatalogLockContention, variant="off")
rollback: set_flag(flag=productCatalogLockContention, variant="on")
severity: sev2
preconditions:
  - the flagd productCatalogLockContention flag is on
  - catalog requests are slow but mostly succeeding
  - product-catalog memory usage is well below its cap (otherwise suspect a GC spiral)
queries:
  - product pages are slow but still loading
  - catalog latency spiked
  - database lock contention on the catalog
  - everything is slow when browsing products
---

# Product catalog lock contention

Lock contention on the catalog's database. Requests queue behind one another, so latency rises
while most requests eventually succeed.

## The distinction that matters

This is the **slow** failure; `product-catalog-failure` is the **broken** one. Both report the
same service.

| | `product-catalog-lock-contention` | `product-catalog-failure` |
|---|---|---|
| Errors | few or none | errors on specific products |
| Latency | **sharply elevated** | roughly normal for successes |
| Scope | all catalog requests | specific products |

And the third candidate, from `docs/decisions/0002-*`: a **memory-ceiling GC spiral** also
produces "slow or unresponsive catalog", but with CPU near 600% and memory at ~95% of cap.
That one is not fixed by any flag.

**Latency without errors is the signature of contention.** An agent that sees elevated latency
and proposes a restart has taken an unnecessary action.

## Detection

Look at the catalog's own span durations rather than at error rates — error-rate alerts will
read near zero while the service is badly degraded. Database spans carry the
`OTEL_SEMCONV_STABILITY_OPT_IN=database` convention on this service, so DB operations are
visible as child spans.

## Blast radius

`checkout`, `frontend` and `recommendation` all call it with `service_started`, so nothing is
blocked from starting; all three slow down at request time.

## Fix

```
set_flag(flag=productCatalogLockContention, variant="off")
```

Variant class: **boolean** (`off | on`).

## Severity

**sev2 PROPOSED** — degraded, not down; customers can still browse and purchase, slowly.
Escalate if latency rises far enough that upstream callers begin timing out, because a
sufficiently slow service is indistinguishable from a broken one. *Confirm against the severity
policy.*

## Provenance

Variants and dependency conditions read from the repo at tag `3.1.0`. The GC-spiral comparison
is measured (ADR-0002). This flag's own behaviour is **not yet observed** — in particular the
claim "few or no errors" needs confirming by injection.
