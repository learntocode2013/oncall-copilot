# ADR-0005: one `set_flag` payload, and an undo that is read rather than computed

**Status:** Accepted
**Date:** 2026-11-06
**Deciders:** P1 (domain, knowledge & memory). **This changes P2's graded component — `planning/plan.py`, `planning/monitor.py`, `stubs.py`, `handoffs.py` — and P5's `oncall-service` demo payloads. P2 and P5 should read it.**
**Builds on:** [ADR-0004](0004-the-only-write-server.md), whose "Recorded, NOT fixed" section this resolves.

## Context

`set_flag` had two incompatible definitions in two owners' packages:

| | payload | inverse |
|---|---|---|
| `oncall_graph/planning/plan.py` | `{flag: str, enabled: bool}` | `not enabled` |
| corpus, validator, `ACTION_SCHEMAS`, `mcp-ops-actions` | `{flag: str, variant: str}` | the runbook's own `rollback` |

Both were internally consistent and fully tested. Neither could fail, because `build_graph` refuses a non-empty tool dict — so in three PRs the planner and the server had never once had to agree. Meanwhile each side was being built on: PR #18's C2 topology added two more `"set_flag paymentFailure enabled false"` strings and log lines asserting `(enabled=true)`, so the two definitions were diverging, not converging.

A boolean cannot express the corpus. 6 of the 18 demo flags have variants beyond `on`/`off`, and six runbook actions name variants like `10sec`, `100%` and `1000x`.

## Decisions

### 1. `{flag, variant}` everywhere, with a guard that compares the two definitions

`ACTION_ARGS["set_flag"]` is now `{"flag": str, "variant": Variant}`.

The change that matters more is `test_the_planner_and_the_write_server_agree_on_every_payload`, which compares `ACTION_ARGS` against `ACTION_SCHEMAS` per action. The guard that was supposed to cover this, `test_actions_match_the_tool_schemas`, only checks that action **names** have a schema — it says nothing about arguments, which is why this drifted silently. The new guard asserts `set_flag` is in both maps before comparing, because a guard that compared two empty intersections would pass forever. That mistake has been made in this repo before, in the first write-isolation guard.

### 2. A variant is not a token

Every other string argument is validated against `_TOKEN` (`^[A-Za-z_][A-Za-z0-9_-]*$`). Measured against the real corpus variants:

```
off   PASS      100%   REJECTED      1000x  REJECTED
on    PASS      10sec  REJECTED      50%    REJECTED
```

So a pure type swap would have produced a planner that validates every fix and refuses every rollback — a failure visible only while undoing, which is the worst moment to find one. Variants are checked against `_VARIANT` (`^[A-Za-z0-9][A-Za-z0-9_.%-]*$`): wider than a token, still no whitespace and none of the characters a shell reads, so `_reject_shell` has nothing left to find. A `Variant` marker type in `ACTION_ARGS` selects the rule, which keeps the choice visible in the schema rather than buried in a key-name comparison.

`flag` remains a `_TOKEN`, and a test asserts the wider rule did not leak across.

### 3. The inverse is read from the forward write, not derived

There is no `not "100%"`, and the variant that re-arms a fault is named by the runbook — in a corpus `oncall_graph` may not import, by the dependency rule that keeps retrieval going through MCP.

So `monitor.code_rollback` restores the `previous` variant that the forward write reported:

```python
last = {"step": {"tool": "set_flag", ...},
        "result": {"variant": "off", "previous": "100%", "changed": True}}
```

`mcp-ops-actions` already returned `previous` for exactly this purpose; `Shop.apply` now returns it too, so the scripted and real paths agree. This is **more** accurate than what it replaces: negation assumed a flag has two states, and `paymentFailure` has seven. `_previous_variant` returns empty unless the last step was the flag write, so a rollback is never built from a read step's payload.

Rejected: having the model supply the restore variant in a structured `rollback` field. More faithful to the runbook, but it turns `validate_action`'s `rollback` from a procedure string into a structured payload, which reaches `stubs._record`, `remediate.apply_command`, the payload shown at approval, and their tests — a larger change to P2's contract for no gain in accuracy, since `previous` is already exact.

### 4. `rollback_flag` is code-only

The undo names `rollback_flag`, and it is deliberately **not** in `ACTION_TOOLS`: `validate_action` rejects it as an unknown action, so a model cannot propose a re-arm. Only `code_rollback` emits it, and `WRITE_TOOLS = ACTION_TOOLS + ("rollback_flag",)` is what `Shop.apply` accepts.

This mirrors ADR-0004's server-side split for the same reason: one tool that can only clear a fault and one that can restore one, so "undo" never reads as "fix". Routing the undo through `set_flag` would also simply fail against the real server, which accepts only `off` pairs through the fix tool.

## Consequences

- `make guards` 28 → 29. Tests 381 → 382.
- `stubs.Shop.flags` is `dict[str, str]`, initialised to the real armed variant `"100%"` rather than `True`. **`"off"` is truthy**, so every `if self.flags.get(...)` became `Shop.armed()` — four sites — and `handoffs.suspects_from_exchanges` compares `change.get("variant") == "off"`. A missed truthiness check reports a cleared flag as a broken shop.
- `_typed_arg`'s `bool` branch is removed: `set_flag.enabled` was its only user, and an unreachable branch in a validator is what this repo keeps finding under "fully tested".
- The scripted shop's log lines changed from `flagd flag paymentFailure is enabled (enabled=true)` to `... variant is 100% (the fault is armed)`. C2's trace assertions moved with them.
- `oncall-service`'s three scripted demo payloads and its rollback string changed. The service has no other dependency on the shape.
- **Still unreconciled, and P2's:** `ACTION_TOOLS` names `restart`, `scale` and `rollback`, which have no schema in `ACTION_SCHEMAS`, no server and no corpus action. The new guard skips an action the contract does not declare, so it does not catch these.
- The contradiction was latent only because `build_graph` refuses tools. It stops being latent the moment the registry meets a topology, which is the next piece of work — this is what had to land first.
