# ADR-0003: The MCP tool registry, and where write isolation is enforced

**Status:** Accepted
**Date:** 2026-11-05
**Deciders:** P1 (domain, knowledge & memory), covering for P3 (tools & guardrails) while they are on leave. **P3 owns this code and should review it on return; P2 must agree the one change it asks of `oncall_graph`.**

## Context

`load_tools(transport) -> ToolRegistry` is `oncall-tools`' documented public surface and the
only path from the graph to retrieval. Nothing implemented it. `permissions.py`,
`hooks/pre_tool.py` and the write-isolation test were all referred to in CONTRIBUTING.md as
though they existed; none did.

Three facts shaped what got built:

> **Partly resolved since.** `ops_actions` landed in #19 ([ADR-0004](0004-the-only-write-server.md)) and `telemetry` in this change ([ADR-0006](0006-mcp-telemetry-and-the-moved-client.md)). `tickets` (8103) remains unwritten.

1. **One of four servers exists.** The October plan names `telemetry` (8101), `runbooks` (8102),
   `tickets` (8103) and `ops_actions` (8104 — the only write server). Only `runbooks_kb` is
   written.
2. **`runbooks_kb`'s two tools do not cover what the solo baseline calls.** `stubs.py` supplies
   `promql`, `log_search`, `traces`, `incident_record` and `status_draft`, and
   `planning/plan.py` names them.
3. **MCP tool schemas arrive as JSON Schema.** `oncall_graph.agent._as_langchain_tool` builds a
   LangChain tool from a function's real Python signature, or passes a `BaseTool` through.

## Decisions

### 1. `load_tools()` lands; `build_graph` is not wired to it yet

`build.py` keeps its `NotImplementedError` for non-empty tools. Pointing the graph at a registry
that supplies 2 of the 7 tools the solo baseline calls would break a working baseline, and
falling back to stubs per missing tool would let an E1 measurement be silently half-stubbed —
which is the same class of failure as a container that passes its health check without a model
loaded.

Wiring is a later PR, after the other three servers exist or after P2 settles the stub/contract
shape (below).

### 2. `ToolSpec` is framework-neutral; the graph adapts it

`load_tools()` returns `ToolSpec(name, description, schema, write, server, invoke)`, not a
LangChain tool. Building LangChain objects here would bind P3's package to P2's agent framework,
so the tool layer could not be tested or reused without it.

**This asks one change of P2:** `_as_langchain_tool` needs a branch for a schema-carrying tool —
`StructuredTool` with an `args_schema` built from `spec.schema` — alongside the two it has. The
alternative, returning LangChain tools so that P2's file needs no change at all, was rejected for
the coupling. P2 decides; the registry does not depend on which way it goes.

### 3. Write marking comes off the wire and fails closed

Each server declares `ToolAnnotations(read_only_hint=...)` per tool, and that is the single source
of truth. `_is_write` returns `True` unless the annotation is *explicitly* `True`: absent
annotations, a missing hint, `False`, and a merely-truthy value all mean write.

A hand-written allowlist in `permissions.py` was rejected: it is a second copy of what the servers
already declare, and the copy is what drifts. `oncall_knowledge.schema` carries a TODO saying
exactly this about its own `ACTION_NAMES`.

Fail-closed means a server author who forgets to annotate gets their tool **withheld** from every
diagnosis agent — visible and annoying — rather than trusted into one.

### 4. Write isolation has two layers, and neither is the network

In HTTP mode `mcp-ops` is just another container on the Compose network; reachability is not a
control.

- `permissions.for_role(registry, "diagnosis")` never puts a write tool in the registry, so the
  model cannot try what it cannot see.
- `hooks.pre_tool` refuses an unapproved write even if one got there.

One is prevention; the other catches the prevention being wrong.

### 5. Approval binds to the exact arguments

`oncall_contracts.tools.fingerprint(tool, args)` is a SHA-256 over the tool name and its
canonicalised arguments. `pre_tool` compares the presented approval's fingerprint against the call
about to run and raises `ApprovalMismatch` on a difference.

An approval bound only to a tool name would let `set_flag adFailure on` run because a human
approved `set_flag paymentFailure off`. Keys are sorted, so two orderings of the same call agree;
an extra argument changes the fingerprint, because an argument `ops_actions` ignores reads as a fix
that was applied.

Three distinct refusals, because a node must be able to tell them apart: `ApprovalRequired` (never
asked — retrying is correct), `ApprovalDenied` (a human said no — retrying is not), and
`ApprovalMismatch` (the approval is stale).

### 6. `SERVERS` lists only servers that exist

Listing the three unwritten servers would make `load_tools()` fail for everyone until all four
landed, or — worse — return fewer tools than it claimed. Adding a server is one entry, in the
commit that adds the server. Asking for an unknown one raises `UnknownServerError` rather than
being skipped.

### 7. One task owns the sessions

An MCP session is a long-lived async context manager, and the graph is synchronous. The registry
runs an event loop on a background thread; one task opens the sessions, publishes the tools, waits,
and closes them.

This was not the first design. Opening the sessions in one task and closing them in another fails
with `RuntimeError: Attempted to exit cancel scope in a different task than it was entered in` —
anyio binds a cancel scope to the task that entered it. Measured, not anticipated.

## Consequences

- `test_actions_match_the_tool_schemas` was skipping and now **passes**: creating
  `oncall_contracts.tools` with `ACTION_SCHEMAS` activated it. One pending guard remains
  (CODEOWNERS).
- A new guard, `test_read_only_servers_still_declare_their_tools_read_only`, builds the real
  servers in-process and reads the real annotations.
- `oncall_knowledge.schema.ACTION_NAMES` can now be sourced from `ACTION_SCHEMAS`, which its TODO
  asks for. Not done here: it is P1's file and a separate change.
- The registry is tested against the real server over the SDK's in-process transport — no port, no
  subprocess, `semantic=False` so no torch — so it is covered by the CI `test` job, not only by
  the container check.

## Rejected

**A `langchain-mcp-adapters` dependency.** It would supply the client and the LangChain conversion
in one step, but it decides the framework question (decision 2) for P2 and it is not obvious that
it preserves `ToolAnnotations`, which decisions 3 and 4 depend on entirely.

**`transport="memory"` as a third transport.** Tests need an in-process client, but widening a
production type for a test seam puts a test-only value in the vocabulary the harness and the
dashboards share. `load_tools(connect=...)` is the seam instead, and nothing in the shipped path
passes it.
