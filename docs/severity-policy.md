# Severity Policy

Owner: P1 — domain, knowledge & memory

The triage agent assigns severity from this document. It is deliberately short, because a
policy nobody can hold in their head is applied inconsistently.

Severity values are fixed by `oncall_contracts.config.Severity`: **`sev1`, `sev2`, `sev3`,
`none`**. There is no sev4 and no "critical" — if you need another level, change the contract
first.

## The levels

| | Definition | Test |
|---|---|---|
| **sev1** | Customers **cannot complete a purchase**, or the shop is unreachable. No workaround. | Would a customer trying to buy right now fail? |
| **sev2** | The purchase path is **degraded but completing**, *or* business data is being lost or delayed. | Can they buy, slowly or unreliably? Are order records incomplete? |
| **sev3** | Something off the purchase path is broken or slow. Customers can buy normally. | Does this stop anyone buying? If no, it is sev3. |
| **none** | **No fault.** Load variation, or a monitoring artifact with no customer impact. | Is anything actually broken? |

"The purchase path" means: browse → add to cart → checkout → payment. Ads, recommendations and
images are **not** on it.

## The rule that matters most

**Severity is assigned from observed customer impact — never from container health, and never
from error rate alone.**

Every one of the three obvious shortcuts is wrong on this system, and each has been measured:

**1. Zero errors does not mean healthy.** A failure severe enough to stall the request produces
*no status code at all* — Envoy returns 504 and no `http_server_request_duration_seconds_count`
sample is recorded. We measured checkout failing 100% of orders while the error-rate expression
read zero. **Check request volume and upstream 504s, not the ratio.**
(See `data/runbooks/payment-failure.md`, `docs/decisions/0002-*`.)

**2. An unhealthy container may be cosmetic or catastrophic.** Identical symptom, opposite
severity, decided entirely by who depends on it:

| | Condition on it | Severity |
|---|---|---|
| `cart` unhealthy | `frontend: service_started` | **sev3** — shop unaffected |
| `recommendation` unhealthy | `frontend: service_healthy` | **sev1** — port 8080 unbound, shop unreachable |

**Check the dependants, not the service.**
(See `data/runbooks/failed-readiness-probe.md`, `docs/decisions/0001-*`.)

**3. A rising error count may be a traffic flood.** Error *count* rises with volume; error
*share* does not. A flood is `none`. But a flood can also push services past resource limits
and cause real faults — so ask *was there a flood?* then *did it break anything?*
(See `data/runbooks/load-generator-flood-homepage.md`.)

## Partial impact

Where a fault affects a proportion of sessions — the percentage-variant flags, `cartFailure`
and `paymentFailure` — use the proportion:

- **≥ 50% of purchase attempts failing → sev1.** Retrying does not reliably help.
- **< 50% → sev2.** Most customers succeed; the rest can retry.

This is a judgement threshold, not a measured one. It is in the policy so the triage agent
applies it consistently rather than inventing a number per incident.

## Escalation and de-escalation

**Escalate to sev1** when any degradation crosses into customers being unable to buy. The
common path is a slow dependency on the purchase path pushing checkout past Envoy's timeout —
at which point the symptom stops being latency and becomes an outage with no error metric.

**Escalate a sev3 service** if something requires it `service_healthy` *and* a restart is
imminent, because the restart will not complete.

**De-escalate only on verified recovery**, measured in a success signal rather than in the
absence of errors. An idle shop also has no errors.

## Cost

Runaway cost with no customer impact is **sev2**, not sev3. It is off the purchase path, but
unlike a broken ad it does not stop getting worse while unattended. This is the one place the
policy departs from pure customer impact, and it is deliberate.

## What is code and what is judgement

Per the project's first rule — *the model proposes; code and people decide* — the mechanical
parts belong in `oncall_graph`, not in a prompt:

**In code:** the 50% partial-impact threshold; the dependant-condition lookup
(`service_healthy` versus `service_started`); whether request volume is non-zero.

**Model judgement:** whether an observed symptom is on the purchase path; whether a degradation
has crossed into unavailability; which of several candidate causes fits the evidence.

A severity the model assigned without consulting the dependant conditions should be treated as
unsupported, the same as a claim with no citation.
