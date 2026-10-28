---
kind: cause
fault_id: ad-high-cpu
status: structural
service: ad
symptom: ad service slow or unresponsive, CPU saturated
severity: sev3
fix:
  action: set_flag
  args:
    flag: "adHighCpu"
    variant: "off"
rollback:
  action: set_flag
  args:
    flag: "adHighCpu"
    variant: "on"
preconditions:
  - the flagd adHighCpu flag is on
  - ad container CPU is saturated while memory stays well below its 300 MiB cap
  - ad is still listening on its port
queries:
  - ad service is pegging the cpu
  - ads are slow to load
  - ad service unresponsive but running
  - high cpu on one service
---

# Ad service CPU saturation

Sustained high CPU load in the ad service. It remains up and listening but responds slowly or
not at all.

## Discriminate from adManualGc

`ad` has two faults producing "ad is slow", plus a third for outright failure:

| | `adHighCpu` | `adManualGc` | `adFailure` |
|---|---|---|---|
| CPU | **sustained high** | **spiky** — bursts during collections | normal |
| Memory | stable, well under 300 MiB | **sawtooth** — grows then drops sharply | stable |
| Errors | few; mostly timeouts | few; latency spikes | **hard errors** |
| Shape | continuous degradation | **periodic** stalls | immediate failure |

The signature is in the *shape over time*, not in a single sample. A memory sawtooth with CPU
bursts is garbage collection; flat memory with pinned CPU is compute load. **One snapshot
cannot separate them** — the agent must look at a range, not an instant.

Compare also the GC spiral in `docs/decisions/0002-*`: that showed pinned CPU *and* memory at
~95% of cap. Here memory should be comfortable, because ad's cap is 300 MiB.

## Blast radius — higher than it looks

| Dependant | Condition |
|---|---|
| `frontend` → `ad` | **`service_healthy`** |

`ad` is one of the few services `frontend` requires to be **healthy**, like `recommendation`.
At runtime that does not stop traffic — Compose `depends_on` only gates startup — but **if the
stack is restarted while this fault is active, `frontend` will not start and the shop becomes
unreachable on port 8080.** That is exactly the cascade in
`docs/decisions/0001-patch-recommendation-healthcheck.md`.

Note ad's health check is a TCP connect (`bash -c 'echo > /dev/tcp/localhost/9555'`), so it
passes as long as the port is open. A CPU-saturated ad can be slow and still report healthy.

## Fix

```
set_flag(flag=adHighCpu, variant="off")
```

Variant class: **boolean** (`off | on`).

## Severity

**sev3 PROPOSED** — ads are not on the purchase path, so customers can still buy. Escalate if
ad latency begins to stall frontend page renders. *Confirm against the severity policy.*

## Provenance

Variants, 300 MiB cap, health check and the `frontend: service_healthy` condition read from the
repo at tag `3.1.0`. **Behaviour not yet observed.** The CPU-versus-memory shapes above are
reasoned from the flag descriptions and from the measured GC spiral in ADR-0002; confirm by
injecting both ad faults and comparing time series.
