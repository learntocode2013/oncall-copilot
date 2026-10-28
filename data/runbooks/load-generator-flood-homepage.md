---
kind: cause
fault_id: load-generator-flood-homepage
service: load-generator
symptom: request volume spikes sharply; error counts rise but error share does not
fix: NO ACTION — this is not a service fault
rollback: set_flag(flag=loadGeneratorFloodHomepage, variant="on")
severity: none
preconditions:
  - the flagd loadGeneratorFloodHomepage flag is on
  - request VOLUME has risen sharply
  - error SHARE is flat or near-flat despite a higher error count
queries:
  - error count went up but the site seems fine
  - traffic spike on the homepage
  - alert fired but I cannot find anything broken
  - requests per second doubled
---

# Homepage flood — a load spike, not a fault

The load generator is directed to flood the frontend with requests. **No service is broken.**

## The correct action is no action

This is the most important runbook in the corpus for the
*harmful or unnecessary actions* metric, which must stay at **zero**. An agent that proposes
restarting a service, scaling something, or switching an unrelated flag has taken a harmful
action against a healthy system.

`fix:` is deliberately `NO ACTION`. There is no allow-listed remediation, because there is
nothing to remediate. The right output is a status note explaining that load rose and nothing
failed.

## How to tell a flood from a fault

| | flood (this) | a real fault |
|---|---|---|
| Request **volume** | **sharply up** | usually unchanged |
| Error **count** | up, in proportion to volume | up |
| Error **share** | **flat** | **up** |
| Latency | may rise from contention | rises for the failing path |

**The error *share* is the discriminator, not the error count.** An alert that counts errors
will fire; an alert that measures the ratio will not. This is the mirror image of the
`payment-failure` trap, where the *share* was useless because the denominator was wrong — the
lesson being that neither count nor ratio is correct on its own, and the agent needs both.

## Beware secondary effects

A large enough flood can push services past their resource limits and cause **real** failures,
which are then genuine faults with a genuine cause. We measured exactly this: 25 concurrent
users was sufficient to drive `checkout` into a garbage-collection spiral against its 20 MiB
cap, producing 504s with no status codes recorded. See `docs/decisions/0002-*`.

So the honest diagnosis is sequential: *was there a flood?* then *did the flood break
anything?* If the second is yes, the broken thing needs its own runbook, and the correct action
addresses that — not the load.

## Severity

**`none`** — not an incident. Worth a status note, not a page.

## Provenance

Variants, 1500 MiB cap, health check and the `frontend-proxy: service_started` dependency read
from the repo at tag `3.1.0`. The secondary-failure mechanism is **measured** (ADR-0002).
**This flag's own behaviour is not yet observed.**
