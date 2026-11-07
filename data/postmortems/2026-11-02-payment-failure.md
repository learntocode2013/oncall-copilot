---
kind: postmortem
incident_id: inc-2026-11-02-01
title: checkout error rate rose and payment transactions stopped
service: payment
symptom: checkout error rate above 5% and payment transactions stop
severity: sev1
lessons:
  - id: les-read-the-flag-baseline-first
    status: approved
    statement: >-
      When checkout returns 422 and payment transactions fall to zero together, read the
      flagd baseline before suspecting a deployment. The two signals move together for a
      flag change and separately for a deploy, and the flag file is one read away.
    service: payment
    preconditions:
      service: payment
      symptom: checkout error rate
    evidence_refs:
      - data/runbooks/payment-failure.md
      - eval/faults/manifest.yaml
---

Authored as seed material for the playbook, from the fault family this project arms on
purpose. The signals below are the ones `eval/faults/manifest.yaml` states for
`payment-failure`, not an observed production incident.

## What happened

With `paymentFailure` armed at `100%`, frontend 422s rise and `demo_payment_transactions_total`
stops advancing. The manifest's detect condition is the ratio of 422s to 422s plus payment
transactions, over a five-minute rate, crossing 0.5; recovery is payment transactions
advancing again at all.

## Why the lesson is about reading, not about concluding

The fix is a flag write, and the approval gate exists so a person makes that call. What the
lesson generalises is the cheap read that distinguishes this family from a deploy
regression, which is the part an investigation can get wrong for several expensive minutes.
