---
kind: cause
fault_id: cart-failure
status: structural
service: cart
symptom: adding items to the cart fails for a proportion of sessions
severity: sev1
fix:
  action: set_flag
  args:
    flag: "cartFailure"
    variant: "off"
rollback:
  action: set_flag
  args:
    flag: "cartFailure"
    variant: "100%"
preconditions:
  - the flagd cartFailure flag is set to a non-off variant
  - cart itself is running and reported healthy
  - failures are proportional, not total, unless the variant is 100%
queries:
  - customers cannot add items to their basket
  - cart errors on a fraction of requests
  - add to cart failing intermittently
  - POST /api/cart returning errors
---

# Cart failures

The cart service rejects a configured percentage of requests. Customers cannot reliably add
items, so sessions abandon before checkout.

## Variant class: percentage

`off | 10% | 25% | 50% | 75% | 90% | 100%`. There is **no `on`**. An invalid variant does not
error — the flag silently never fires, and the run looks like a healthy system.

The variant matters diagnostically: at `10%` the symptom is intermittent and easily mistaken
for client-side flakiness; at `100%` the cart is simply broken. **Match the observed failure
proportion against the variant** before concluding.

## Blast radius

| Dependant | Condition | Effect |
|---|---|---|
| `checkout` → `cart` | `service_started` | none at startup; checkout fails at runtime when it cannot read the cart |
| `frontend` → `cart` | `service_started` | none at startup |

Nothing requires cart to be *healthy*, so a cart fault does not prevent anything from starting.
The damage is entirely at request time.

## Detection

Cart failures appear on `POST /api/cart` and `GET /api/cart` at the frontend, and as gRPC
errors on cart's own spans. Because checkout reads the cart, a cart fault can *present* as a
checkout failure — see `checkout-error-rate-elevated` for the candidate table.

Discriminator against the payment faults: cart errors appear on **cart** spans and affect
`/api/cart` requests, which the payment faults do not touch at all.

## Fix

```
set_flag(flag=cartFailure, variant="off")
```

**Expected effect:** `/api/cart` error rate returns to zero. Cart has a 160 MiB cap and is not
one of the services affected by the memory-ceiling defect in `docs/decisions/0002-*`.

## Severity

**sev1 PROPOSED** at high variants — customers cannot purchase, and no workaround exists.
Arguably **sev2** at `10%`–`25%` where most sessions succeed. *Confirm against the severity
policy.*

## Provenance

Variants, cap and dependency conditions read from `deploy/flagd/demo.flagd.json` and
`compose.yaml` at tag `3.1.0`. **Behaviour not yet observed** — the fault has not been
injected. The discriminator above is reasoned, not measured.
