---
kind: cause
fault_id: product-catalog-failure
service: product-catalog
symptom: product pages fail to load for specific products
fix: set_flag(flag=productCatalogFailure, variant="off")
rollback: set_flag(flag=productCatalogFailure, variant="on")
severity: sev1
preconditions:
  - the flagd productCatalogFailure flag is on
  - product-catalog memory usage is well below its cap (otherwise suspect a GC spiral)
  - failures are scoped to specific products, not all catalog requests
queries:
  - product pages are not loading
  - product catalog returning errors on some items
  - specific product detail page is broken
  - catalog service erroring
---

# Product catalog failures

The catalog service fails on a specific product. Product detail pages error; browsing other
products still works.

## Check the memory ceiling first

**This service has a history.** `product-catalog` ships a 20 MiB cap with `GOMEMLIMIT=16MiB`
against a working set measured at 29–34 MiB under load. Without the override in
`docs/decisions/0002-raise-product-catalog-memory-ceiling.md` it enters a garbage-collection
spiral — 623% CPU, never binds its port, health check never passes.

So "product catalog not responding" has **three** candidate causes, and they look similar:

| Cause | Distinguishing evidence |
|---|---|
| this flag | errors scoped to **specific products**; service healthy; CPU normal |
| `productCatalogLockContention` | **latency** rises across the board; no hard errors |
| memory ceiling (ADR-0002) | **CPU near 600%**, memory at ~95% of cap, port not listening, health check failing |

Check `docker stats product-catalog` before proposing either flag. If CPU is pegged and memory
is at its ceiling, neither flag is the cause and switching one off will not help.

## Blast radius

| Dependant | Condition |
|---|---|
| `checkout` → `product-catalog` | `service_started` |
| `frontend` → `product-catalog` | `service_started` |
| `recommendation` → `product-catalog` | `service_started` |

Nothing requires it *healthy*, so an unhealthy catalog does not block startup — but three
services call it at request time, so failures fan out widely.

## Fix

```
set_flag(flag=productCatalogFailure, variant="off")
```

Variant class: **boolean** (`off | on`).

## Severity

**sev1 PROPOSED** — product pages are the shop's primary surface. *Confirm against the
severity policy.*

## Provenance

Variants, cap, `GOMEMLIMIT`, health check and dependency conditions read from the repo at tag
`3.1.0`. The memory-ceiling behaviour was **measured first-hand** (ADR-0002). The flag's own
behaviour is **not yet observed**.
