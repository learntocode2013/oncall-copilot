---
kind: postmortem
incident_id: inc-2026-10-26-01
title: recommendation never became healthy, so frontend never started
service: recommendation
symptom: container recommendation is unhealthy and frontend will not start
severity: sev2
lessons:
  - id: les-health-check-names-a-binary
    status: approved
    statement: >-
      When a container is unhealthy with no application error, read the health check
      itself before reading the application. A check that invokes a binary fails
      identically to a broken service, and the failure text names the binary, not the
      service.
    service: recommendation
    preconditions:
      service: recommendation
      symptom: unhealthy
    evidence_refs:
      - docs/decisions/0001-patch-recommendation-healthcheck.md
      - "observed failing streak: 45"
  - id: les-unhealthy-but-up-check-recent-changes
    status: rejected
    statement: >-
      When a service is unhealthy but up, check recent flag and configuration changes
      first.
    service: recommendation
    reason: >-
      True here and actively misleading on inc-2026-10-27-01, where nothing had changed
      and the cause was a memory ceiling the service could not fit under. The
      precondition that separates the two cases is "a configuration or flag change
      precedes the alert", and precondition_matches evaluates only service and symptom,
      so that precondition cannot be stated in a form code will check. Kept as a
      counterexample: a lesson whose guard cannot be expressed is not safe to approve.
    evidence_refs:
      - docs/decisions/0001-patch-recommendation-healthcheck.md
      - docs/decisions/0002-raise-product-catalog-memory-ceiling.md
---

Authored as seed material for the playbook, from an event recorded in this repository's
own decision log. It is not a synthetic incident and not an observed production one: the
facts below come from ADR-0001.

## What happened

The first `make up` against the pinned demo (`3.1.0`, `dedc017`) failed with
`dependency failed to start: container recommendation is unhealthy`. The health check at
that tag is `nc -z localhost ${RECOMMENDATION_PORT}`, and `nc` is not in the image, so
every attempt failed with `/bin/sh: 1: nc: not found`. The check could never pass.
`frontend` declares `recommendation: condition: service_healthy`, so the failure
presented as a frontend outage.

## Why it reads as a service failure

Nothing in the application was wrong. The evidence that pointed at the health check was
in the check's own output, which is not where an investigation of a frontend outage
normally looks.

## The rejected lesson

The obvious generalisation — "unhealthy but up means something changed" — is the one this
incident most invites and the one the next incident punished. It is kept with its reason
rather than deleted, because the reason is the useful part.
