# ADR-0004: `mcp-ops-actions`, the only write server

**Status:** Accepted
**Date:** 2026-11-06
**Deciders:** P1 (domain, knowledge & memory), covering for P3 (tools & guardrails) while they are on leave. **P3 owns this code; P4/P5 own the lab file this moves; P2 owns a contradiction this records but does not fix.**
**Builds on:** [ADR-0003](0003-mcp-tool-registry-and-write-isolation.md)

## Context

Write isolation had two layers and nothing to isolate. `permissions.for_role` filtered writes out of a diagnosis registry and `hooks.pre_tool` refused unapproved writes, but no server served a write tool — so every assertion about either was true by there being nothing to test it on.

## Decisions

### 1. Two tools, `set_flag` and `rollback_flag`

The corpus splits perfectly cleanly, and this was measured, not assumed:

- **every `fix` in all 18 runbooks is `variant: "off"`** — fixes only ever turn a fault off
- **every `rollback` restores the fault variant** (`on`, `100%`, `10sec`, `1000x`)
- the two sets of (flag, variant) pairs are **disjoint**: 17 of 17 rollback pairs appear in no fix

A single `set_flag` accepting both would be a tool that can arm every fault in the corpus, with nothing but a variant string separating "fix the payment service" from "break it". Two tools mean a plan that wants to re-arm a fault must name `rollback_flag`, and that name travels into the registry, the approval payload and the audit trail.

This replaced an earlier plan for one tool over fix ∪ rollback. That plan was justified as "the agent cannot arm a fault it was asked to diagnose" — a property the data shows it would not have had at all, since every rollback pair is a fault-arming pair.

### 2. Each tool accepts only (flag, variant) pairs the corpus names

Not "any existing flag to any existing variant". The allowlist is built from the runbooks at startup: 16 fix pairs and 17 rollback pairs. The agent cannot invent a variant (`paymentFailure -> 50%` exists in flagd and is refused), cannot touch a flag no runbook knows, and cannot route a re-arm through the fix tool.

`emitRawPii` is one of only two demo flags no runbook names. A flag that leaks personal data is exactly the thing an agent must have no route to, and the allowlist denies it without anyone having written a rule about PII.

**The allowlist is not the primary control.** The approval gate is, and the write annotations are what keep these tools out of every diagnosis registry. The allowlist limits the damage an approved-but-wrong call can do.

### 3. The flagd writer moved to `oncall-contracts`

`oncall_lab.flags` already implemented it — atomic replace, mode preserved, flag and variant validated. Two packages now write that document with opposite intent: the lab injects a fault, this server fixes one. Nothing may import the lab (it holds the fault manifest's root cause), and a second copy in `oncall-tools` would mean a flagd schema change needs fixing twice.

`ALLOWED_IMPORTS["oncall_lab"]` gains `oncall_contracts`. This does not weaken the lab's isolation: the rule that matters is that nothing imports the **lab**, and that is unchanged. Contracts imports nothing, so there is no cycle.

### 4. Two flagd documents, and that is not an accident

- the **committed baseline**, copied into the image, which `ingest_runbooks()` validates the corpus against and which the allowlist is derived from
- the **live document**, mounted read-write at `/etc/flagd`, which this server edits and flagd watches

The first attempt used one path — the live document — for both. It fails, because the validator rejects a document with any fault armed: *"flag 'paymentFailure' is committed as '100%', not 'off'; a fault left enabled in the baseline breaks the shop for everyone who pulls."* That rule is about the committed baseline, so validating against the live file would make this server refuse to start **whenever a fault is active — during every incident it exists to resolve.** Three tests failed that way before it was found.

Separating them is also the better boundary: the allowlist comes from the committed, validated corpus, so editing the live document cannot widen what the agent may do. Variants are static definitions; only `defaultVariant` changes at runtime.

### 5. Its own Dockerfile, with no torch

344 MB against `mcp-runbooks`' 1.66 GB. This server reads the corpus but never embeds anything, so it needs no `semantic` extra. One shared Dockerfile would put a 1.4 GB embedder in the write server.

Build-only in `make images`, like `oncall-lab`: its runtime needs `deploy/flagd` mounted read-write, and the script's rule is *run what needs no mounts, build what does*. Its startup path is covered by `test_ops_actions.py` instead, which builds the server against a `tmp_path` copy of the real document.

## Recorded, NOT fixed: `set_flag` has two incompatible definitions

> **Resolved since.** See `0005-one-set_flag-payload.md`. The planner now takes
> `{flag, variant}` and the inverse is read from the write's `previous`, which is
> the approach this section recommended. The analysis below is kept as written.

`oncall_graph/planning/plan.py` declares `ACTION_ARGS = {"set_flag": {"flag": str, "enabled": bool}}`. The corpus, the validator, `oncall_contracts.tools.ACTION_SCHEMAS` and this server all use `{"flag": str, "variant": str}`.

**`{flag, enabled: bool}` cannot express the corpus.** 6 of the 18 demo flags have variants beyond `on`/`off` — `aiSlowResponse: [10sec, 5sec, off]`, `cartFailure: [10%, 25%, 50%, 75%, 90%, 100%, off]`, `emailMemoryLeak: [1x, 10x, 100x, 1000x, 10000x, off]` and three more — and six runbook actions name variants like `10sec`, `100%` and `1000x`.

This was going to be fixed here as a one-line change to `ACTION_ARGS`. It is not one line: `plan.py:default_rollback` derives the inverse of a `set_flag` **by negating the boolean**, which has no meaning for a variant, and `stubs.py` models the scripted shop's flags as `dict[str, bool]` with a `set_flag(flag, enabled, expected_effect)` tool. Converting it means reshaping P2's planner inverse logic and the solo baseline's flag model — P2's graded component, and not P1's to redesign while covering for a third owner.

**For P2:** the inverse of a fix is not computable by negation. It is the runbook's own `rollback` action, and `ops_actions.set_flag` returns the `previous` variant for exactly this purpose.

Also unreconciled: `plan.py`'s `ACTION_TOOLS` names `restart`, `scale` and `rollback`, which have no schemas in `ACTION_SCHEMAS`, no server, and no corpus action. `test_actions_match_the_tool_schemas` does not catch this because it checks the corpus's `ACTION_NAMES`, not the planner's `ACTION_TOOLS`.

## Consequences

- `make guards` 26 → 28. `test_the_write_server_declares_its_tools_as_writes` is new, and `test_no_diagnosis_registry_holds_a_write_tool` now runs against a registry that actually holds writes rather than a synthetic one.
- `load_tools()` returns 4 tools from 2 servers. `registry.writes()` is non-empty for the first time.
- A pre-existing order-dependent test was exposed and fixed: `test_asking_for_semantic_without_the_extra_raises` skipped on `sys.modules` (had it been *imported*) rather than `find_spec` (is it *installed*), so it passed only when an earlier test happened to load the module.
