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

### Resolved: retrieval reaches P2 through MCP, and the rules now say so

**Settled 2026-11-04 by P1, with P3 on leave. P3 and P2 should review on return.**

The rules used to contradict the architecture in both directions, which is recorded below
because the shape of the mistake is worth keeping.

The architecture says P2 reaches retrieval through MCP: `oncall-graph` calls `load_tools()`,
which talks to `mcp-runbooks` (`oncall_tools.servers.runbooks_kb`, port 8102 — *"Search runbooks
and past postmortems. Read-only."*). P2 never sees `Retriever`.

`ALLOWED_IMPORTS` in `tests/test_guards.py` says the opposite, in both directions:

```python
"oncall_tools": {"oncall_contracts"},                                      # cannot import knowledge
"oncall_graph": {"oncall_contracts", "oncall_knowledge", "oncall_tools"},  # can
```

So the package that needs retrieval is forbidden it, and the package that should only ever see
MCP tools is free to bypass them. `runbooks_kb` will fail the dependency guard on its first real
line, and nothing currently stops P2 importing `oncall_knowledge.retrieval` directly instead.

Both changes are now applied in `ALLOWED_IMPORTS`, and the guard enforces the architecture
instead of contradicting it:

1. **`oncall_tools` gains `oncall_knowledge`.** The MCP server becomes a thin wrapper over
   `Retriever`, which is what "retrieval.py is the only public surface" was for.
2. **`oncall_graph` loses `oncall_knowledge`.** Worth doing on its own merits: without it,
   nothing prevents one E1 topology retrieving through MCP while another imports directly — and
   the comparison between them would then differ in retrieval as well as in orchestration, which
   is exactly what E1 exists to isolate.

Removing `oncall_knowledge` from `oncall_graph` broke nothing: checked before the change, no
file under `packages/oncall-graph/` imported it.

Neither change was P1's to make alone, and with P3 on leave P1 made both rather than leave the
server unwritable. The mitigation is that they were applied **together**: on its own, adding
`oncall_knowledge` to `oncall_tools` is a guard loosened to fit code, which `CLAUDE.md` rules
out. Paired with the removal from `oncall_graph`, the net effect is a tighter rule set that
matches the design. If P3 disagrees with the seam, the server is ~150 lines and the revert is
one commit.

### The `mcp-runbooks` tool contract

**P1 proposes this; P3 owns the server.** Same split as the P2/P3 seam below: the schema is the
dependency, the implementation is the owner's. P1 will not write
`oncall_tools/servers/runbooks_kb/` — it is P3's graded component, and the guard change it needs
should not be landed by the package that wants it.

Two tools, both `read`:

```python
search_runbooks(query: str, k: int = 5) -> list[RunbookHit]
get_runbook(document_id: str) -> Runbook
```

```python
RunbookHit = {
    "document_id": str,  # "payment-failure"
    "status": str,  # verified | structural | hypothesis
    "service": str,
    "severity": str,
    "excerpt": str,  # first ~60 words of the body
    "score": float,  # fused; comparable only WITHIN one response
    "lexical_rank": int | None,
    "semantic_rank": int | None,
}
Runbook = RunbookHit | {"body": str, "fix": dict | None, "preconditions": list[str]}
```

Six things this contract pins, each for a reason:

**Two tools, not one.** Median runbook body is ~440 tokens, so returning five full bodies costs
~2,200 tokens per search. Search returns excerpts; the agent fetches the one it wants. If P3
measures that the round trip hurts more than the tokens, collapsing to one tool is theirs to
decide — but the default should be the cheap one.

**`status` is returned and never filtered on** (design D13). 14 of 18 runbooks are `structural`
— behaviour never actually observed — and 2 are `hypothesis`. The agent must be able to say "the
only runbook I found is unverified"; it cannot if retrieval silently dropped them. Equally, a
`sev1` runbook must not rank higher for containing the word "sev1", which is why `severity` is
returned but is not index text.

**Both pre-fusion ranks travel with the hit.** `lexical_rank=None, semantic_rank=1` means only
the embedding found this; that is the difference between a confident single-source hit and
agreement between two retrievers, and it is the only way to debug a bad answer after the fact.

**`score` is comparable within one response and nowhere else.** It is an RRF sum over
`1/(k0+rank)`; it has no absolute meaning, no stable scale across queries, and must never be
thresholded. Document that in the tool description so the model is not invited to.

**No `service` or `status` filter arguments.** Both are tempting and both would let the agent
narrow to what it already believes, which is the failure mode the whole corpus exists to break:
it is how a wrong hypothesis gets confirmed. Filtering is a ranking decision and belongs to the
commander's reasoning, over results it can see.

**The server builds its own `Retriever` and holds the documents**, which is how it answers
`service`, `severity` and `body` — `RetrievalResult` carries only `document_id`, `status`,
`score` and the two ranks. No change to `oncall-knowledge` is needed. Build it once at startup:
`Retriever.build(ingest_runbooks(), semantic=True)` loads an 80 MB model and takes ~10 seconds,
so per-request construction would be visible in every trace.

Ingestion is root-pinned to `data/runbooks/`, so the server cannot reach `eval/` even if asked.
That is enforced in `ingest_runbooks`, not here.

## Public surfaces

Keep these small. Everything else in a package is internal and may change freely.

| Package | Public API |
|---|---|
| `oncall-graph` | `build_graph(tools, store, *, topology) -> CompiledStateGraph` — nothing else |
| `oncall-tools` | `load_tools(transport) -> ToolRegistry`; the hook callables |
| `oncall-knowledge` | `Retriever.build(documents, *, semantic, k0)` / `.retrieve(query, k)` / `.search(query, k)`; memory-store interfaces to come. Everything under `indexing/` is internal |
| `oncall-service` | the HTTP API (not a Python API) |
| `oncall-eval` | a CLI; imported by nobody |

## The three E1 configurations

`build_graph` takes a keyword-only `topology`: `c0_workflow` (fixed sequence, no agent
decisions), `c1_single` (one ReAct loop holding all tools) or `c2_team` (supervisor plus
specialists). They live side by side in `oncall_graph/topologies/`.

**Grow C1 into C2 by adding a topology, never by mutating one.** Experiment E1 compares all
three on the same model, tools and budget, so every one of them has to stay runnable for the
life of the project. C1 gets written first — it is the week-1 vertical slice — but as
`topologies/c1_single.py`, not as "the graph".

**C1 and C2 share `nodes/` and `planning/`.** If each carried its own hypothesis ranking, E1
would be measuring node quality instead of topology and the comparison would be worthless.

Every run writes a `RunRecord` carrying `config_id` and `complexity`. Group by those two and
you get the E1 table. Without both, the only answer available is one aggregate number, which
cannot support a claim like "the team helps on multi-service incidents but costs more on
simple ones".

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
