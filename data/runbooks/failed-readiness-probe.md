---
kind: cause
fault_id: failed-readiness-probe
service: cart
symptom: cart reported unhealthy while continuing to serve requests
fix: set_flag(flag=failedReadinessProbe, value=false)
rollback: set_flag(flag=failedReadinessProbe, value=true)
severity: sev3
preconditions:
  - the flagd failedReadinessProbe flag is enabled
  - the service answers a direct connection on its port despite being reported unhealthy
  - no dependant service requires this service to be `service_healthy`
queries:
  - cart is unhealthy but the shop still works
  - service reported unhealthy, should I restart it
  - health check failing but requests are succeeding
  - readiness probe failing on cart
---

# Readiness probe failure, service still serving

The cart service's readiness probe fails while the service continues to answer requests. This
is a **monitoring fault, not a service fault.**

## Do not restart the service

The obvious remediation is wrong here, and acting on it counts against the
*harmful or unnecessary actions* metric, which must stay at zero.

Before proposing anything, establish two things:

**1. Is the service actually serving?** Connect to its port directly. Exit 0 means it is
listening and the probe is lying:

```
bash -c 'echo > /dev/tcp/localhost/7070'     # exit 0 = listening
bash -c 'echo > /dev/tcp/localhost/9999'     # exit 1 = control, nothing there
```

**2. Does anything actually depend on its health?** This determines whether the alert matters
at all, and the answer differs sharply by service:

| Dependency | Condition | Effect of an unhealthy state |
|---|---|---|
| `checkout` → `cart` | `service_started` | none — checkout keeps working |
| `frontend` → `cart` | `service_started` | none — the shop keeps working |
| `frontend` → `recommendation` | **`service_healthy`** | frontend never starts; port 8080 unbound; **entire shop unreachable** |

So an unhealthy `cart` is cosmetic, while an unhealthy `recommendation` is total outage. The
symptom string is identical. **Check the dependants, not just the service.**

## Why this class is hard to see

A service in this state reports every conventional signal as fine. From our own incident:

```
OOMKilled: false    RestartCount: 0    ExitCode: 0    Status: Up (unhealthy)
```

Nothing in metrics says "broken". The container is running, has never crashed, and is
serving traffic. The only thing wrong is the probe — and a probe that cannot pass looks
identical to a service that cannot start.

Logs do not help either: these services ship logs via OTLP to OpenSearch rather than stdout,
and several images are distroless and read-only, so there is no shell to investigate from.

## Fix

```
set_flag(flag=failedReadinessProbe, value=false)
```

**Expected effect:** the container's health state returns to `healthy` within a few probe
intervals. Request success rate should not change, because it was never affected.

**Rollback:** `set_flag(flag=failedReadinessProbe, value=true)`.

## Severity

**sev3** when nothing gates on the service's health — no customer impact, and the only cost is
a misleading signal.

**Escalate to sev1** if a dependant requires `service_healthy`, because the dependant will not
start and the user-facing effect is a full outage.

## Provenance

The dependency conditions above were read from `compose.yaml` at tag `3.1.0`. The
"serving while unhealthy" behaviour, the direct-connection discriminator, and the misleading
container signals were observed first-hand during a real incident on this stack — see
`docs/decisions/0001-patch-recommendation-healthcheck.md`.

**Not yet observed:** what this flag specifically does to cart's probe. The fault has not been
injected. Confirm before relying on the symptom wording.
