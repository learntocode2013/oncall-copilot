# ADR-0001: Patch the demo's recommendation health check in our overlay

**Status:** Accepted
**Date:** 2026-10-26
**Deciders:** P5 (deployment & observability), with P4 (evaluation) — the pin affects comparability of results

## Context

The system under test is the OpenTelemetry Astronomy Shop, vendored as a submodule pinned
to tag `3.1.0` (commit `dedc017`) per the repo structure design, Decision 3. The pin exists
so that every owner measures against identical behaviour: held-out comparisons are repeated
three times and their spread reported, which is meaningless if the system under test drifts
between machines.

The first `make up` against that pin failed:

```
dependency failed to start: container recommendation is unhealthy
```

Investigation found the health check at `3.1.0` is:

```yaml
test: ["CMD-SHELL", "nc -z localhost ${RECOMMENDATION_PORT}"]
```

and `nc` is **not present in the image** — the check fails with `/bin/sh: 1: nc: not found`
on every attempt (observed failing streak: 45). It can never pass.

`frontend` declares `recommendation: condition: service_healthy`, so `frontend` and
`frontend-proxy` never start, port `8080` stays unbound, and the entire shop is unreachable.
`docker compose up --wait` therefore fails and `make bootstrap` exits non-zero.

**The service itself is healthy.** Verified inside the container, with a control:

| Probe | Exit |
|---|---|
| `bash -c 'echo > /dev/tcp/localhost/9001'` | 0 — listening |
| same against closed port `9999` | 1 — correctly fails |

Only the probe was broken. Upstream has already fixed this on `main`, replacing `nc` with a
bash `/dev/tcp` redirect and raising `start_period` from 10s to 20s. `3.1.0` is the **newest
tag**, so there is no release to upgrade to; `main` is 130 commits ahead.

## Decision

Override `recommendation.healthcheck` in `deploy/compose.oncall.yaml`, using upstream's own
fix from `main`. Keep the submodule pinned to `3.1.0`.

```yaml
services:
  recommendation:
    healthcheck:
      test: ["CMD-SHELL", "bash -c 'echo > /dev/tcp/localhost/${RECOMMENDATION_PORT}'"]
      start_period: 20s
```

`bash` is at `/usr/bin/bash` in the image. `sh` is dash and cannot do `/dev/tcp`, so the probe
must invoke bash explicitly. The same idiom is already used by the `ad` service in the demo's
own `compose.yaml` at this tag, so this is the file's established pattern rather than an
invention of ours.

## Options Considered

### Option A: Patch the health check in our overlay (chosen)

| Dimension | Assessment |
|---|---|
| Complexity | Low — six lines in a file we already own |
| Reproducibility | Preserved — the pin stays at a named release |
| Blast radius | One health check on one service |
| Visibility | High — the deviation lives in our repo, reviewed like any change |

**Pros:** keeps the pin at a named tag, so "we ran against 3.1.0" stays true and
`service_version` in telemetry matches the stated version; the deviation is explicit and
reviewable; scoped to exactly the confirmed defect.

**Cons:** we now carry a patch against upstream, which must be removed when the pin moves;
a reader who does not find this ADR will wonder why the overlay touches a demo service.

### Option B: Move the pin to a commit on `main`

| Dimension | Assessment |
|---|---|
| Complexity | Low — one `git checkout` in the submodule |
| Reproducibility | Preserved in form (a SHA pins) but weakened in substance |
| Blast radius | 130 commits of change to the system under test |
| Visibility | Low — the reason is buried in a submodule bump |

**Pros:** no patch to maintain; picks up any other fixes made since `3.1.0`.

**Cons:** trades one known, understood, confirmed defect for 130 commits of unknown change
to the exact component our evaluation measures. "We pinned `dedc017` plus 130 commits of
main" is a materially weaker claim in a report than "we pinned release 3.1.0".

### Option C: Drop `--wait` from `up` and `bootstrap`

**Pros:** the command stops failing immediately.

**Cons:** rejected as a symptom fix. `--wait` is what makes `make verify` meaningful and is
the basis of the clean-machine install evidence the course guide requires at the final
review. Removing it hides the failure rather than resolving it, and would have left the shop
silently unreachable on port 8080.

## Trade-off Analysis

The decision turns on what the pin is *for*. It is not hygiene — it exists so P2's tuning runs
and P4's scored runs measure the same system, and so the held-out comparison's reported spread
reflects model variance rather than a drifting shop. Option B keeps a pin that is technically
reproducible while discarding the property that motivated it. Option A keeps that property and
pays for it with a small, documented, removable patch.

The patch is also cheap to be wrong about: it changes a health check, not behaviour. If the
probe were incorrect the symptom is immediate and loud (a container reported unhealthy), not
subtle corruption of a result.

## Consequences

**Easier:** `make bootstrap` completes; `make verify` reports real health; the shop is
reachable on 8080; the install evidence is a command rather than a claim.

**Harder:** we maintain a deviation from a pinned dependency. Anyone bumping the submodule
must check whether the patch is still needed.

**To revisit:** when a release after `3.1.0` ships containing upstream's fix, move the pin and
delete the override. `deploy/compose.oncall.yaml` carries a `REMOVE THIS` marker pointing here.

## Related, unresolved

`product-catalog` was also unhealthy on the first run (nothing listening on `:3550`, CPU at
0.03%, so blocked rather than spinning). It **resolved on its own** after a clean
`--force-recreate` and is not addressed by this ADR. It was not root-caused; the most likely
explanation is startup timing against `astronomy-db`, whose health gate had only just opened.
**It may recur.**

Investigation was blocked because the container is distroless and read-only (no shell) and the
demo ships logs via OTLP to OpenSearch rather than stdout — and OpenSearch is published on a
random host port, not a fixed one. If it recurs, publish OpenSearch on a fixed port first.

Worth noting for the agent's own design: *a service that is started but unhealthy, with no
stdout logs, whose only evidence lives in OpenSearch* is exactly the incident the On-Call
Copilot must diagnose. This is first-hand evidence that the `telemetry` MCP server needs
OpenSearch log search, not metrics and traces alone.

## Action Items

1. [x] Override `recommendation.healthcheck` in `deploy/compose.oncall.yaml`
2. [x] Add `-f deploy/compose.oncall.yaml` to `COMPOSE` in `deploy/Makefile`
3. [x] Verify: 22 containers healthy, `make verify` green, `8080` bound by `frontend-proxy`
4. [ ] Watch for `product-catalog` recurring; if it does, publish OpenSearch on a fixed port
       before investigating
5. [ ] On any submodule bump, check whether the override is still required and delete it if not
