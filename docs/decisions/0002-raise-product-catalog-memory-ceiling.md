# ADR-0002: Raise product-catalog's memory ceiling in our overlay

**Status:** Accepted
**Date:** 2026-10-27
**Deciders:** P5 (deployment & observability), with P4 (evaluation) — a second deviation from the pinned system under test
**Resolves:** the "Related, unresolved" item in [ADR-0001](0001-patch-recommendation-healthcheck.md)

## Context

`product-catalog` was intermittently `unhealthy` with nothing listening on `:3550`. It first
appeared on the very first `make up` in **full** mode, seemed to resolve after a
`--force-recreate`, then recurred in **minimal** mode. ADR-0001 recorded it as not
root-caused.

It is now root-caused. At tag `3.1.0` the service ships:

```yaml
deploy: { resources: { limits: { memory: 20M } } }
environment: [ GOMEMLIMIT=16MiB ]
```

Its real working set is **~28 MiB**. `GOMEMLIMIT` is a **soft** limit, so the Go runtime does
not fail — it garbage-collects continuously trying to stay beneath a ceiling it cannot fit
under, burning cores while making no forward progress. It never completes initialisation, so
it never binds its port and its gRPC health probe can never pass.

Measured on this stack:

| | Status | CPU | Memory |
|---|---|---|---|
| `20M` / `GOMEMLIMIT=16MiB` (upstream) | unhealthy, failing streak 45 | **623%** | 16.89 / 20 MiB (84%) |
| `128M` / `96MiB` (first probe) | healthy | 2.83% | 27.69 / 128 MiB |
| `64M` / `48MiB` (chosen) | healthy | 5.37% | 20.78 / 64 MiB (32%) |

A **220x** CPU reduction. No OOM kill occurs at any point, because the frantic collection
*succeeds* at staying under the container cap — which is why `OOMKilled: false`,
`RestartCount: 0` and `ExitCode: 0` all looked reassuring while the service was unusable.

The intermittency is explained: the service was always marginal, so startup sometimes won the
race against the GC and sometimes did not. An earlier CPU sample of 0.03% led us to wrongly
dismiss memory as a cause; that sample was taken while the process was blocked rather than
collecting.

**There is no upstream fix to adopt.** Unlike ADR-0001, `main`'s configuration for this service
is byte-identical — same `20M`, same `GOMEMLIMIT=16MiB`, same health check. Verified. So
moving the pin would not help.

## Decision

Override `product-catalog`'s memory limit in `deploy/compose.oncall.yaml`:

```yaml
product-catalog:
  environment: [ GOMEMLIMIT=48MiB ]
  deploy: { resources: { limits: { memory: 64M } } }
```

`64M` / `48MiB` preserves upstream's ~80% `GOMEMLIMIT`-to-cap ratio while clearing the ~28 MiB
the service actually needs. Note the heap settles *smaller* at `48MiB` (20.78 MiB) than at
`96MiB` (27.69 MiB), because Go sizes the heap against the limit — so the tighter ceiling costs
slightly more GC (5.37% vs 2.83% CPU) for a smaller resident set. Both are negligible against
the 623% baseline.

## Options Considered

### Option A: Raise the ceiling in our overlay (chosen)

| Dimension | Assessment |
|---|---|
| Complexity | Low — seven lines in a file we already own |
| Reproducibility | Preserved — the pin stays at `3.1.0` |
| Blast radius | One resource limit on one service |
| Evidence | Unambiguous: 623% → 5.37%, unhealthy → healthy |

**Pros:** fixes a stack that otherwise intermittently fails to start for all five owners;
consistent with the approach already taken in ADR-0001; explicit and reviewable.

**Cons:** a second deviation from pinned upstream; two patches is where a reader starts
wondering how much of the system under test is ours.

### Option B: Move the pin to a commit on `main`

**Rejected on evidence, not preference.** `main`'s `product-catalog` config is byte-identical,
so this fixes nothing while importing 130 commits of unrelated change to the component our
evaluation measures.

### Option C: Raise Docker Desktop's memory allocation

**Rejected — it addresses the wrong constraint.** Total memory was never short: actual usage
was 3.75 GiB of a 7.748 GiB VM, with ~4 GiB free while the service was spiralling. The ceiling
is self-imposed per-service.

### Option D: Accept it and retry `make up` until it wins the race

**Rejected.** It makes `--wait` unreliable, which makes `make verify` meaningless, which
removes the clean-machine install evidence the course guide requires at the final review. It
also trains the team to ignore a failing health check — the opposite of what this project is
about.

## Trade-off Analysis

We now carry two patches against a pinned dependency, which is worth being uncomfortable
about. The mitigating factors: both are tightly scoped, both are documented with a reproduction
and a measurement, both carry `REMOVE THIS` markers, and neither changes application behaviour
— one fixes a probe, the other raises a resource ceiling. Nothing about product diagnosis,
telemetry content or fault injection is altered, so evaluation validity is untouched.

The alternative on offer was a system under test that fails to start unpredictably. That would
have cost far more than two documented overrides, and would have been blamed on five different
laptops before anyone found the cause.

## Consequences

**Easier:** `make up` is deterministic in both modes; `--wait` is trustworthy; `make verify` is
real evidence; no owner loses an afternoon to "it works on my machine".

**Harder:** two deviations to re-check on any submodule bump.

**To revisit:** if upstream raises the limit, delete the override. Report it upstream (see
Action Items) so that becomes possible.

## Note on minimal mode

This bug is **not** minimal-mode-specific — it first bit us in full mode. Minimal mode made it
more likely by increasing contention, which is how we came to root-cause it.

Separately: `MODE=minimal` saves ~1 GiB of *declared* caps (7.71 → 6.66 GiB) but actual usage
was 3.75 GiB against a 7.748 GiB VM. Since the real constraint was a per-service ceiling rather
than total memory, teammates should run `MODE=full` and measure before assuming they need
minimal.

## Action Items

1. [x] Override `product-catalog` memory and `GOMEMLIMIT` in `deploy/compose.oncall.yaml`
2. [x] Verify at `64M` / `48MiB`: healthy, CPU 5.37%, 0 unhealthy containers
3. [x] Update ADR-0001's unresolved item to point here
4. [ ] Report upstream to `open-telemetry/opentelemetry-demo` — we have a clean reproduction
       and measurements, and the fix is a one-line limit change. Worth doing: it is a real
       contribution and a defensible line in the evaluation report.
5. [ ] On any submodule bump, re-check whether either override is still required
