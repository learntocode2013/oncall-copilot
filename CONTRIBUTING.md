# Contributing

The short version lives in [README.md](README.md#how-to-contribute). This file is the reference
for the rules CI enforces and the mechanics that trip people up.

## Enforced by CI

Six jobs: `lint`, `test`, `guards`, `lockfile`, `eval-fixture` and `images`. The first five run
Python; `images` runs Docker and is described under *Containerising a server* below.

## Enforced by `make guards`

These tests encode graded requirements. The `guards` CI job is not allowed to fail.

| Guard | Why it exists |
|---|---|
| `test_nothing_imports_oncall_eval` | If the agent can import the harness it can reach the fault manifest, which holds the correct answer for every case. |
| `test_eval_does_not_import_graph` | P4 drives the system over HTTP so measurement stays at the system boundary and P4 cannot reach inside what P2 is tuning. |
| `test_heldout_never_referenced_outside_eval` | Keyed on the *path*, so prose discussing held-out methodology does not break the build while `eval/held_out/` cannot evade the guard. |
| `test_ingestion_globs_exclude_the_eval_tree` | Retrieval must never be able to index the answers. Every glob stays rooted in `data/`. |
| `test_every_package_has_a_codeowner` | A package added later with no owner silently drops the review separations. |
| `test_read_only_servers_still_declare_their_tools_read_only` | Write marking comes off the wire and fails closed, so the scheme rests on each server declaring `read_only_hint` honestly. Builds the real servers in-process and reads the real annotations. |
| `test_actions_match_the_tool_schemas` | Every action a runbook names must exist in `oncall_contracts.tools.ACTION_SCHEMAS`. Was pending; activated when that module landed. |
| `test_the_write_server_declares_its_tools_as_writes` | The opposite mistake to the row above: a tool that changes the system while declaring `read_only_hint=True` fails closed into the WRONG state, and would be handed to every diagnosis agent. |
| `test_no_diagnosis_registry_holds_a_write_tool` | The graded requirement. Runs against the real servers, and asserts the filtering removed something rather than that there was nothing to remove. |

**If a guard fails, do not weaken the guard.** It is reporting a real leak. Fix the code.

## Dependency direction

```
oncall-contracts  ◀──  knowledge, tools, graph, service, eval
knowledge, tools  ◀──  graph
graph             ◀──  service
service           ◀──  eval          (over HTTP, never by import)
eval              ◀──  nothing
lab               ◀──  nothing       (pages the service over HTTP; holds the fault manifest)
                                 lab itself imports contracts, for the shared flagd writer
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
| `oncall-tools` | `load_tools(transport) -> ToolRegistry` returning framework-neutral `ToolSpec`s; `permissions.for_role` / `assert_read_only`; `hooks.pre_tool.pre_tool`. `registry.SERVERS` lists only servers that exist |
| `oncall-knowledge` | `Retriever.build(documents, *, semantic, k0)` / `.retrieve(query, k)` / `.search(query, k)`; memory-store interfaces to come. Everything under `indexing/` is internal |
| `oncall-service` | the HTTP API (not a Python API) |
| `oncall-eval` | a CLI; imported by nobody |
| `oncall-lab` | the HTTP lab on port 8020; imported by nobody |

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

## The tool registry, and the one thing it asks of `oncall-graph`

`load_tools()` is the only path from the graph to retrieval: `oncall_graph` may import
`oncall_tools` but **not** `oncall_knowledge`, enforced by `make guards`. See
[ADR-0003](docs/decisions/0003-mcp-tool-registry-and-write-isolation.md) for the reasoning.

```python
with load_tools() as registry:  # $MCP_TRANSPORT, else stdio
    tools = for_role(registry, "diagnosis")  # reads only; writes are not in it
    pre_tool(tools["search_runbooks"], args)  # returns quietly for a read
    hits = tools["search_runbooks"].invoke({"query": "checkout 422", "k": 5})
```

A `ToolSpec` carries `name`, `description`, the server's own `schema` (JSON Schema, passed
through untouched), `write`, `server` and a synchronous `invoke`. It is **not** a LangChain tool:
building those here would bind the tool layer to the agent framework.

**This asks one change of P2, not yet made.** `agent._as_langchain_tool` needs a third branch,
for a tool carrying a JSON Schema rather than a function with typed parameters — a
`StructuredTool` with an `args_schema` built from `spec.schema`. Until then the registry and the
graph do not meet, and `build_graph` still refuses non-empty tools on purpose.

**⚠ `set_flag` has two incompatible definitions, and P2 owns the fix.**
`planning/plan.py` declares `ACTION_ARGS = {"set_flag": {"flag": str, "enabled": bool}}`; the
corpus, the validator, `ACTION_SCHEMAS` and `mcp-ops-actions` all use `{flag: str, variant: str}`.
A boolean **cannot express this corpus**: 6 of 18 demo flags have variants beyond `on`/`off`
(`cartFailure: [10%, 25%, 50%, 75%, 90%, 100%, off]`, `emailMemoryLeak: [1x … 10000x, off]`, …)
and six runbook actions name variants like `10sec` and `1000x`. It is not a one-line change:
`default_rollback` derives a `set_flag`'s inverse **by negating the boolean**, and `stubs.py`
models the scripted shop's flags as `dict[str, bool]`. The inverse of a fix is not computable by
negation — it is the runbook's own `rollback`, and `ops_actions.set_flag` returns `previous` for
exactly that. `ACTION_TOOLS` also names `restart`, `scale` and `rollback`, which have no schema,
no server and no corpus action.

**Also unresolved, and P2's call:** the stub `search_runbooks(symptom) -> {"passages": [...]}` in
`oncall_graph/stubs.py` is not the real contract (`search_runbooks(query, k) -> [hits]`, `fix` as
a mapping, plus `status` and the two pre-fusion ranks). The stub has no `status` field at all,
which is the one D13 exists for — the agent cannot say "the only runbook I found is unverified"
if the shape it reads cannot carry that.

**Write isolation is two layers, neither of them the network.** `for_role` keeps a write tool out
of a diagnosis registry so the model cannot try what it cannot see; `pre_tool` refuses an
unapproved write even if one got there. Marking comes from each server's own
`ToolAnnotations(read_only_hint=...)` and **fails closed**: anything not explicitly `True` is a
write, so forgetting to annotate withholds a tool rather than trusting it.

**Approval binds to the exact arguments**, via `oncall_contracts.tools.fingerprint`. An approval
bound to a tool name would let `set_flag adFailure on` run because someone approved
`set_flag paymentFailure off`.

## `mcp-ops-actions`: the only write server

Port 8104. Two tools, both writes, both approval-gated. See
[ADR-0004](docs/decisions/0004-the-only-write-server.md).

```python
set_flag(flag, variant)  # fixes only: every corpus fix is variant="off"
rollback_flag(flag, variant)  # RE-ARMS the fault; every corpus rollback does
```

**Two tools because the corpus splits cleanly.** Every `fix` in all 18 runbooks is
`variant: "off"`; every `rollback` restores the fault variant (`on`, `100%`, `10sec`, `1000x`).
The two sets of pairs are disjoint — 17 of 17 rollback pairs appear in no fix. One tool accepting
both would be able to arm every fault in the corpus, with nothing but a variant string separating
a fix from a re-arm.

**Each tool accepts only (flag, variant) pairs the corpus names** — 16 fix pairs, 17 rollback
pairs, built from the runbooks at startup. `paymentFailure -> 50%` exists in flagd and is
refused. `emitRawPii`, one of two demo flags no runbook names, is unreachable. The allowlist is
not the primary control (the approval gate is); it limits what an approved-but-wrong call can do.

**Two flagd documents, deliberately.** The committed baseline is copied into the image and is
what `ingest_runbooks()` validates against and what the allowlist comes from; the live document
is mounted **read-write** at `/etc/flagd` and is what the server edits. Using one path for both
fails: the validator rejects a document with any fault armed, so the server would refuse to start
during every incident it exists to resolve.

**Its own Dockerfile**, `Dockerfile.ops-actions`, with no `semantic` extra: 344 MB against
`mcp-runbooks`' 1.66 GB. Build-only in `make images` because its runtime needs that mount.

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

Ports: incident API `8000`, lab `8020`, MCP servers `8101`–`8104`. Do not use 8080, 8081, 8089, 8090, 8013,
8016, 4000, 3000, 9090, 9200, 16686 or 4317 — the demo claims all of them.

### A stdio server inherits almost nothing, and a refusal says almost nothing

Two things do not cross the agent-to-server boundary unless you make them. Both were live in
`main` and invisible to 371 passing tests, because every test either ran a server in this process
or called its functions directly.

**Environment.** `StdioServerParameters(env=None)` does not mean "inherit": the SDK substitutes an
allowlist of `HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, `USER`. Sound as a default — a tool server
should not inherit the agent's credentials — but it silently drops whatever configures the server.
`mcp-ops-actions` lost `FLAGD_CONFIG`, fell back to `DEFAULT_FLAG_PATH`, wrote the **committed
baseline** instead of the live document, and returned `changed: true`. A server that needs a
variable declares it on its `ServerSpec`:

```python
ServerSpec("ops_actions", "oncall_tools.servers.ops_actions", 8104, env=("FLAGD_CONFIG",))
```

Only the named variables are forwarded, and only to that server — `mcp-runbooks` has no business
knowing where the writable document lives.

**Refusal text.** The SDK sorts a tool's exceptions in two: an `mcp` `ToolError` is deliberate and
its message reaches the caller; anything else is a crash, and its message *stays on the server* by
design. So a policy refusal raised as a plain `ValueError` arrives as `Error executing tool
set_flag`, with a traceback logged as though the server had broken. **Raise `ToolError` for any
refusal the agent is supposed to learn from** — the allowlist's "this tool accepts `['off']` for
`'paymentFailure'`" is a correction signal, and withheld it just invites the same call again.

Tests for either live in `packages/oncall-tools/tests/test_wire.py`, which is the one place that
spawns a server as a subprocess. Put a test there when the thing under test is the boundary rather
than the server.

### Containerising a server: sync the member, not the root

`uv sync` in a server's Dockerfile must pass `--package oncall-<name>`. Without it uv syncs the
**root** project, which depends on all seven workspace members and therefore needs every
member's source in the build context — so the image either carries packages the server never
imports, or fails outright with `Distribution not found at file:///app/packages/oncall-eval`.
That is how `mcp-runbooks` broke: it was written when only four members were real
distributions, and PR #11 made three more.

Extras resolve against whatever `--package` names, so a server needing `oncall-knowledge`'s
`semantic` extra needs a forwarding entry of its own (`oncall-tools` has one). And once the venv
exists, put it on `PATH` and stop using `uv run` — `uv run` re-targets the root project and
fails the same way, at runtime, on every container start.

The `images` CI job now builds all three images and starts the two that need no mounts
(`mcp-runbooks` gets a real MCP session asserting it serves *hybrid* and not the baseline;
`oncall-lab` is build-only because its runtime needs `eval/faults/manifest.yaml` mounted, which
belongs to compose). Run it yourself with `make images` — it needs Docker, takes ~2 minutes, and
is the only check that can catch an image that does not build or a container that binds its port
without working. Before it existed, five green checks sat on top of both.

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
own paths. After any overlay change, run `make config` and read the rendered output. Paths are
interpolated, so `oncall-service`'s build context is this repo. Values whose names contain
`API_KEY`, `_KEY`, `TOKEN`, `SECRET`, or `PASSWORD` are replaced with `<redacted>`.

## Before you push

```bash
make lint && make test
```

## Commit messages

Say why, not just what. End the subject under ~70 characters, then a body if the change needs
one. If a commit encodes a decision, add an ADR under `docs/decisions/` and reference it.
