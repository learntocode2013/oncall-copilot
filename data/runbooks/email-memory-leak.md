---
kind: cause
fault_id: email-memory-leak
status: structural
service: email
symptom: email service memory grows steadily until it fails
severity: sev2
fix:
  action: set_flag
  args:
    flag: "emailMemoryLeak"
    variant: "off"
rollback:
  action: set_flag
  args:
    flag: "emailMemoryLeak"
    variant: "1000x"
preconditions:
  - the flagd emailMemoryLeak flag is set to a non-off variant
  - email memory is RISING over time rather than sitting at a steady level
  - order confirmation emails are delayed or missing
queries:
  - email service memory keeps growing
  - order confirmation emails are not being sent
  - email service restarting repeatedly
  - memory leak on one service
---

# Email service memory leak

Memory in the email service grows until the container hits its 100 MiB cap.

## Variant class: multiplier

`off | 1x | 10x | 100x | 1000x | 10000x`. **Not percentages and not boolean.** The variant sets
the leak *rate*, so it determines how long until failure — at `10000x` the service may exhaust
100 MiB in seconds; at `1x` it may take a long time. **Time-to-failure is a function of the
variant**, which matters when reproducing.

## Not the same as a GC spiral

This is a **real leak against a hard cap**, which is mechanically different from the Go
soft-limit pathology in `docs/decisions/0002-*`:

| | `email-memory-leak` (Ruby) | GC spiral (Go, ADR-0002) |
|---|---|---|
| Memory over time | **rises monotonically** | **flat at ~95% of cap** |
| CPU | normal until failure | **pinned, 268–623%** |
| Outcome | **OOM kill and restart** | no kill; stalls forever |
| `RestartCount` | **increments** | stays 0 |

`RestartCount` is the cheapest discriminator: a leak gets killed and restarted; a spiral never
does. An agent that sees "memory high" and concludes "leak" without checking the trend or the
restart count will be wrong half the time on this stack.

## Blast radius

| Dependant | Condition |
|---|---|
| `checkout` → `email` | **`service_healthy`** |

`checkout` requires email to be healthy, so a restart while this fault is active prevents
checkout from starting — and checkout is on the purchase path. Higher blast radius than "emails
are late" suggests.

## Fix

```
set_flag(flag=emailMemoryLeak, variant="off")
```

Note the flag stops the leak; it does not reclaim memory already leaked. If the container has
not yet been OOM-killed, memory stays high until it restarts.

## Severity

**sev2 PROPOSED** — customers can still purchase, but confirmations are unreliable and checkout
is at risk on restart. *Confirm against the severity policy.*

## Provenance

Variants, 100 MiB cap, health check and the `checkout: service_healthy` condition read from the
repo at tag `3.1.0`. The GC-spiral comparison is **measured** (ADR-0002). **This flag's
behaviour is not yet observed** — the OOM-kill outcome is inferred from the hard cap.
