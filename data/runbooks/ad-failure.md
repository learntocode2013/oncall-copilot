---
kind: cause
fault_id: ad-failure
status: structural
service: ad
symptom: ad service returns hard errors
severity: sev3
fix:
  action: set_flag
  args:
    flag: "adFailure"
    variant: "off"
rollback:
  action: set_flag
  args:
    flag: "adFailure"
    variant: "on"
preconditions:
  - the flagd adFailure flag is on
  - ad returns errors rather than being merely slow
  - ad CPU and memory are both normal
queries:
  - ad service is returning errors
  - ads fail to load on product pages
  - ad service erroring but not slow
  - errors from the ad service
---

# Ad service failure

The ad service fails outright. Requests return errors rather than being slow.

## Discriminate from the two slow ad faults

`ad` has three faults. Only this one produces **hard errors with normal resource use**:

| | `adFailure` | `adHighCpu` | `adManualGc` |
|---|---|---|---|
| Errors | **hard errors** | few; timeouts | few; latency spikes |
| CPU | **normal** | pinned | bursty |
| Memory | **normal** | flat | sawtooth |

If CPU or memory look abnormal, this is not the fault — see `ad-high-cpu.md`.

## Blast radius

`frontend` requires `ad` to be **`service_healthy`**. The health check is a TCP connect
(`bash -c 'echo > /dev/tcp/localhost/9555'`), so **ad can return errors on every request and
still report healthy** — the port is open. The alert will come from error rates, not from
container health.

At runtime nothing is blocked, but a stack restart while this fault is active prevents
`frontend` from starting and leaves port 8080 unbound. Same cascade as
`docs/decisions/0001-patch-recommendation-healthcheck.md`.

## Fix

```
set_flag(flag=adFailure, variant="off")
```

Variant class: **boolean** (`off | on`).

## Severity

**sev3 PROPOSED** — ads are off the purchase path. *Confirm against the severity policy.*

## Provenance

Variants, 300 MiB cap, health check and dependency condition read from the repo at tag `3.1.0`.
**Behaviour not yet observed.**
