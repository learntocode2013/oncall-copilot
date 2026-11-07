---
kind: postmortem
incident_id: inc-2026-10-27-01
title: product-catalog garbage-collected itself to a standstill under a soft ceiling
service: product-catalog
symptom: unhealthy with nothing listening on the service port
severity: sev2
lessons:
  - id: les-a-soft-limit-never-fails
    status: approved
    statement: >-
      A Go service under GOMEMLIMIT does not fail when it cannot fit: the runtime
      collects continuously and makes no forward progress, so initialisation never
      completes and the port is never bound. Read CPU against working set before
      believing an exit code, because ExitCode 0, OOMKilled false and RestartCount 0 all
      look reassuring in exactly this case.
    service: product-catalog
    preconditions:
      service: product-catalog
      symptom: nothing listening
    evidence_refs:
      - docs/decisions/0002-raise-product-catalog-memory-ceiling.md
      - "limit 20M, GOMEMLIMIT 16MiB, working set ~28MiB"
  - id: les-force-recreate-resolves-intermittent-unhealthy
    status: inferred
    statement: >-
      An intermittently unhealthy container is resolved by docker compose
      --force-recreate.
    service: product-catalog
    preconditions:
      service: product-catalog
      symptom: unhealthy
    evidence_refs:
      - docs/decisions/0001-patch-recommendation-healthcheck.md
---

Authored as seed material for the playbook, from an event recorded in this repository's
own decision log. The facts below come from ADR-0002.

## What happened

`product-catalog` was intermittently `unhealthy` with nothing listening on `:3550`. At tag
`3.1.0` the service ships a 20M memory limit with `GOMEMLIMIT=16MiB`, and its real working
set is about 28 MiB. `GOMEMLIMIT` is a soft limit, so the Go runtime never failed — it
garbage-collected continuously trying to stay under a ceiling it could not fit beneath,
burning CPU while making no progress. It never bound its port, so its gRPC health probe
could never pass.

## Why the unreviewed lesson is still unreviewed

In ADR-0001 this same symptom appeared to resolve after a `--force-recreate`, and then
recurred. `les-force-recreate-resolves-intermittent-unhealthy` records that belief at the
time it was held, with status `inferred`: nobody has reviewed it, and the evidence now
suggests the recreate changed the timing rather than the cause. It is in the corpus so the
store has an unreviewed lesson to refuse, and it must never reach a brief.
