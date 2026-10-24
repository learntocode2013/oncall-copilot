# Contributing

The short version lives in [README.md](README.md#how-to-contribute). This file is the reference
for the rules CI enforces and the mechanics that trip people up.

## Enforced by `make guards`

These five tests encode graded requirements. The `guards` CI job is not allowed to fail.

| Guard | Why it exists |
|---|---|
| `test_nothing_imports_oncall_eval` | If the agent can import the harness it can reach the fault manifest, which holds the correct answer for every case. |
| `test_eval_does_not_import_graph` | P4 drives the system over HTTP so measurement stays at the system boundary and P4 cannot reach inside what P2 is tuning. |
| `test_heldout_never_referenced_outside_eval` | Keyed on the *path*, so prose discussing held-out methodology does not break the build while `eval/held_out/` cannot evade the guard. |
| `test_ingestion_globs_exclude_the_eval_tree` | Retrieval must never be able to index the answers. Every glob stays rooted in `data/`. |
| `test_every_package_has_a_codeowner` | A package added later with no owner silently drops the review separations. |

**If a guard fails, do not weaken the guard.** It is reporting a real leak. Fix the code.

## Dependency direction

```
oncall-contracts  ◀──  knowledge, tools, graph, service, eval
knowledge, tools  ◀──  graph
graph             ◀──  service
service           ◀──  eval          (over HTTP, never by import)
eval              ◀──  nothing
```

`oncall-contracts` exists to break a cycle. If `IncidentState` lived in `oncall-graph`, then P4
and P5 would both have to import P2's package, and the eval/tuning separation would collapse.

## Public surfaces

Keep these small. Everything else in a package is internal and may change freely.

| Package | Public API |
|---|---|
| `oncall-graph` | `build_graph(tools, store) -> CompiledStateGraph` — nothing else |
| `oncall-tools` | `load_tools(transport) -> ToolRegistry`; the hook callables |
| `oncall-knowledge` | retriever + memory-store interfaces |
| `oncall-service` | the HTTP API (not a Python API) |
| `oncall-eval` | a CLI; imported by nobody |

## The P2 / P3 seam

`interrupt()` is a LangGraph primitive, so the **call site** is in `oncall_graph/nodes/remediate.py`
(P2), but the **approval payload schema and permission check** are P3's. The payload type lives
in `oncall_contracts.tools`; `remediate.py` only constructs and passes it.

Settle this before either of you starts. Otherwise you both edit the most heavily-graded file in
the repo — approval binding to the exact payload is a live-demo requirement.

## MCP servers: one entry point, two transports

```bash
# development — the graph spawns each server as a subprocess
MCP_TRANSPORT=stdio

# Docker — each server is its own container
MCP_TRANSPORT=http
```

Every server is runnable as a module (`python -m oncall_tools.servers.telemetry`) in both modes.
`registry.py` reads the transport from config, so `build_graph(tools=load_tools())` is identical
either way and P2 never knows which transport is live.

Ports: incident API `8000`, MCP servers `8101`–`8104`. Do not use 8080, 8081, 8089, 8090, 8013,
8016, 4000, 3000, 9090, 9200, 16686 or 4317 — the demo claims all of them.

**Write isolation is enforced in `permissions.py` and `hooks/pre_tool.py`, not by network
topology.** In HTTP mode `mcp-ops` is just another container on the Compose network. Those two
files carry a graded requirement, and there must be a test asserting no diagnosis agent's
registry contains a tool marked `write`.

## Compose, when editing `deploy/`

Relative paths in **every** `-f` file resolve against the **first** file's directory — which is
the demo repo. So our overlay must use `${ONCALL_ROOT}`-rooted paths:

```yaml
build:
  context: ${ONCALL_ROOT:?set ONCALL_ROOT to the repo root}
```

`--project-directory` does not fix this: it moves resolution for both files and breaks the demo's
own paths. After any overlay change, run `make config` and read the rendered output.

## Before you push

```bash
make lint && make test
```

## Commit messages

Say why, not just what. End the subject under ~70 characters, then a body if the change needs
one. If a commit encodes a decision, add an ADR under `docs/decisions/` and reference it.
