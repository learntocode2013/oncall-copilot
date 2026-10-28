---
kind: cause
fault_id: ad-manual-gc
service: ad
symptom: ad service stalls periodically with memory sawtooth and CPU bursts
fix: set_flag(flag=adManualGc, variant="off")
rollback: set_flag(flag=adManualGc, variant="on")
severity: sev3
preconditions:
  - the flagd adManualGc flag is on
  - ad memory shows a sawtooth over time rather than a steady level
  - CPU is bursty rather than continuously pinned
queries:
  - ad service stalls every few seconds
  - periodic latency spikes on one service
  - memory sawtooth on the ad service
  - garbage collection pauses causing timeouts
---

# Ad service forced garbage collection

Full manual garbage collections are triggered repeatedly in the ad service. Each collection
stalls the service briefly, so latency arrives in periodic spikes rather than as steady
degradation.

## This is the pair partner of adHighCpu

See the comparison table in `ad-high-cpu.md`. The short version:

- **periodic** stalls, **bursty** CPU, **sawtooth** memory → `adManualGc`
- **continuous** degradation, **pinned** CPU, **flat** memory → `adHighCpu`

**A single metric sample cannot tell these apart.** Both show "high CPU, service slow" at an
instant. The discriminator is the shape over a window — which means the diagnosis requires a
range query, not a point query, and is a good test of whether the telemetry tool returns
series rather than scalars.

## Not the same as a GC spiral

`docs/decisions/0002-*` describes a *different* GC pathology: a Go service pinned against
`GOMEMLIMIT` collecting continuously and never making progress. That one has memory at ~95% of
cap and CPU pinned flat — no sawtooth, because the heap never gets to grow.

| | `adManualGc` (this) | GC spiral (ADR-0002) |
|---|---|---|
| Memory | sawtooth, well under cap | **pinned at ~95% of cap** |
| CPU | bursty | **continuously pinned** |
| Recovers between events | **yes** | no |
| Fixed by a flag | **yes** | **no** — needs a resource-limit change |

Mistaking the second for the first costs an unnecessary action, because no flag will fix it.

## Blast radius

Same as `ad-high-cpu`: `frontend` requires `ad` to be **`service_healthy`**, so a stack restart
while this fault is active prevents `frontend` from starting and leaves port 8080 unbound.

## Fix

```
set_flag(flag=adManualGc, variant="off")
```

Variant class: **boolean** (`off | on`).

## Severity

**sev3 PROPOSED** — ads are off the purchase path. *Confirm against the severity policy.*

## Provenance

Variants, cap, health check and dependency condition read from the repo at tag `3.1.0`. The
GC-spiral comparison is **measured** (ADR-0002). **This flag's behaviour is not yet observed**
— the sawtooth-versus-pinned distinction is reasoned and must be confirmed by injecting both
ad faults and comparing time series.
