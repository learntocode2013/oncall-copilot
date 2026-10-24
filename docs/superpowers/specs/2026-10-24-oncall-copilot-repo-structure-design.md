# On-Call Copilot — Repository Structure Design

- **Date:** 2026-10-24
- **Status:** Approved
- **Scope:** Repository and build structure for the Agentic AI Capstone, Project 4 (On-Call Copilot)
- **Team:** 5 owners (the course guide assumes 6 — see Decision 0)

## 1. Context

We are building the On-Call Copilot capstone: when an alert fires, use real telemetry to
identify what failed and why, propose one safe fix for an engineer to approve, verify the
result, and document the incident.

The system under test is the OpenTelemetry Astronomy Shop demo, whose `flagd` feature flags
inject faults. Because we inject the faults, the true root cause and correct fix are known in
advance — this is the project's central evaluation advantage and the reason the fault injector
is a first-class component rather than test scaffolding.

This document covers **repository structure only**. The agent's behavioural design (prompt
contents, hypothesis ranking, lesson schema) is deliberately out of scope and belongs in
per-component specs.

### Requirements this structure must satisfy

| Source | Requirement |
|---|---|
| Guide, L7 | At least 3 MCP servers; every tool marked read or write; write access only to the agent that needs it |
| Guide, L8 | LangGraph graph, typed state, conditional edges, self-correction loop, persistent checkpointer |
| Guide, L12 | Reflexion loop; lessons from development cases only; playbook frozen before held-out run |
| Guide, L13 | 60+ cases split ~20 dev / 40+ held-out; held-out stored where the agent cannot read it |
| Guide, L16 | `docker compose up` starts app, MCP servers, vector store, Prometheus, Grafana; prompts and model IDs in one versioned config; eval runs in CI and fails below threshold |
| Guide, roles | Two separations: the person tuning the agent must not write the held-out cases; the person building the defences must not write the attacks |
| Guide, submission | One-command README; redrawn architecture diagram; threat model; memory policy; eval report (4–6 pp); experiment log; contribution log; slides; backup demo recording |

## 2. Decisions

### Decision 0 — Five owners against a six-role guide

The guide defines six roles; we are five. Only one merge preserves the two graded separations,
because both land on the Evaluation & red-team role, which therefore cannot be merged into
orchestration, tools or security. We merge roles 1 and 2 (domain lead + knowledge engineer),
the one pairing where the work is genuinely the same pipeline: the domain lead authors the
runbooks and postmortems the knowledge engineer ingests.

| Owner | Role | Lectures |
|---|---|---|
| P1 | Domain, knowledge & memory (roles 1+2) | L4–L6, L9 |
| P2 | Orchestration, planning & improvement | L8, L10–L12 |
| P3 | Tools, human control & security | L7, L14, L15 |
| P4 | Evaluation & red team — **do not merge** | L13, L15 |
| P5 | Deployment & observability | L13, L16 |

**Action:** confirm with the instructor that a team of five is permitted. Team rules come from
the cohort's separate *Capstone Project Guidelines*, which the build guide does not override.

### Decision 1 — uv workspace, one package per architectural layer

**Chosen.** Five layer packages plus a shared contracts package, each with its own
`pyproject.toml`, resolved by a single root `uv.lock`.

*Rationale:* ownership is visible in the dependency graph rather than in directories nobody
imports; each CI job installs only its layer's dependencies (P1's reranker pulls heavy ML
wheels that P5's service job has no use for); and the eval/tuning separation becomes a
structural property rather than a convention.

*Rejected:* a single package (boundaries are convention only; every CI job installs every
dependency; merge conflicts concentrate in one `pyproject.toml`). A two-package hybrid
(app + eval) was the runner-up and remains the fallback if six `pyproject.toml` files prove
to be more ceremony than the team wants.

*Cost accepted:* six `pyproject.toml` files, and a uv-workspace learning curve in week 0.

### Decision 2 — Held-out cases in-repo, guarded by CI

**Chosen.** `eval/heldout/` lives in the repo, owned by P4 alone via `CODEOWNERS`, excluded
from the ingestion glob, and protected by import/leak tests that fail the build.

*Rationale:* the agent has no filesystem tool by design (no shell — that is signature
challenge B), so the real threat is not a reader but **silent contamination** along four
routes: the retrieval index sweeping in case files; a Reflexion lesson written from a held-out
incident; a human iterating after peeking; and variants of one fault straddling the dev/held-out
split. Contamination makes scores go *up* and raises no error, so it must be caught by a test
that fails loudly. The guard doubles as viva evidence for "how do you know the playbook was
frozen?".

*Rejected:* a separate private repo as a submodule (true access control, but submodule
mistakes cost student hours, and a teammate cloning without `--recurse-submodules` makes the
eval suite silently half-run). Encryption with age/git-crypt (key distribution is another
moving part; a lost key loses the held-out set; opaque diffs make review impossible).

**Required guards** (CI, must pass):

```
test_eval_is_a_leaf                        # nothing imports oncall_eval
test_eval_does_not_import_graph            # P4 drives the system via HTTP, not imports
test_heldout_never_referenced_outside_eval # grep all packages except oncall-eval
test_ingestion_glob_excludes_eval_dir      # retrieval cannot index the answers
test_playbook_frozen_before_heldout        # playbook hash recorded in the run record
```

### Decision 3 — The demo repo is a pinned git submodule under `vendor/`

**Chosen.** `vendor/opentelemetry-demo/` pinned to one tag; our Compose overlay lives in our
repo and uses `${ONCALL_ROOT}`-rooted paths.

*Rationale:* the system under test is part of the experimental setup. Prometheus labels carry
`service_version`; if P4 scores 60 incidents against one demo version and P2 tunes against
another, the numbers are not comparable — and the guide requires the held-out comparison be
repeated three times with the spread reported. A pinned submodule makes the system under test
a versioned dependency, and an upstream bump becomes a reviewable commit.

*Rejected:* separate clone with a path in `.env` (nothing pins the version). Forking the demo
and developing inside it (our five owners' commits would be buried among thousands of upstream
ones, and the contribution log is a graded artifact).

*Cost accepted:* `git clone --recurse-submodules`, and submodule checkout enabled in CI.

### Decision 4 — Markdown docs in repo, PDFs built at submission, slides external

**Chosen.** Prose deliverables are `.md` so they diff, review and merge like code, with a
pandoc target producing submission PDFs. Slides are authored in Google Slides and only the
exported PDF is committed. The experiment log is **generated**, never hand-typed.

*Rationale:* five people writing prose need real diffs and mergeable text. Report numbers are
read from `artifacts/runs/` by a make target, which makes transcription drift between the
harness output and the report structurally impossible. Slide layout is where markdown tooling
fights back, and the presentation is graded on delivery, so that one artifact stays in a
WYSIWYG tool.

*Rejected:* committing `.docx`/`.pptx` (binary diffs; simultaneous edits unmergeable; numbers
copy-pasted by hand). Markdown for everything including slides via Quarto (one more toolchain
to learn in week 0, and markdown slides constrain layout).

## 3. Repository layout

```
oncall-copilot/
├── pyproject.toml                  # workspace root: [tool.uv.workspace]
├── uv.lock                         # ONE lock for all members
├── .python-version                 # 3.12
├── .env.example                    # keys + ports; .env gitignored
├── .gitmodules
├── CODEOWNERS
├── Makefile                        # the one-command story
├── CONTRIBUTING.md                 # import rules, branch + review policy
├── README.md
│
├── packages/
│   ├── oncall-contracts/           # shared types; depends on NOTHING
│   │   └── src/oncall_contracts/
│   │       ├── state.py            # IncidentState TypedDict
│   │       ├── tools.py            # MCP tool in/out schemas, approval payload
│   │       ├── faults.py           # fault manifest schema (lists + complexity)
│   │       ├── runrecord.py        # per-run record: config_id x complexity
│   │       └── config.py           # Topology, Complexity, model IDs, budgets
│   │
│   ├── oncall-knowledge/      P1
│   │   └── src/oncall_knowledge/
│   │       ├── ingest.py           # runbooks + postmortems -> chunks
│   │       ├── index.py            # BM25 + vectors, RRF, reranker
│   │       ├── query.py            # one query-side technique
│   │       └── memory/
│   │           ├── store.py        # incidents + lessons
│   │           └── preconditions.py# lesson applicability check
│   │
│   ├── oncall-graph/          P2
│   │   └── src/oncall_graph/
│   │       ├── __init__.py         # exports build_graph() and nothing else
│   │       ├── build.py            # build_graph(tools, store) -> CompiledStateGraph
│   │       ├── commander.py        # scope -> hypotheses -> test -> act
│   │       │                       #       -> verify -> communicate
│   │       ├── routing.py          # conditional edges, Literal[...] returns
│   │       ├── checkpoint.py       # SQLite/Postgres checkpointer
│   │       ├── nodes/
│   │       │   ├── triage.py       # impact, severity, recent changes
│   │       │   ├── diagnose.py     # fan-out, one per suspect service
│   │       │   ├── runbook.py      # retrieve + check preconditions
│   │       │   ├── remediate.py    # propose -> interrupt() -> run -> verify -> roll back
│   │       │   └── comms.py        # status note, postmortem draft
│   │       ├── planning/
│   │       │   ├── hypotheses.py
│   │       │   └── replan.py       # contradiction -> replan   <- challenge A
│   │       ├── improvement/
│   │       │   ├── reflexion.py
│   │       │   └── playbook.py     # lesson store, freeze + hash
│   │       └── topologies/        # the three E1 configurations, siblings
│   │           ├── c0_workflow.py  # fixed sequence, no agent decisions
│   │           ├── c1_single.py    # one ReAct loop, all tools (week-1 slice)
│   │           └── c2_team.py      # supervisor + specialists
│   │
│   ├── oncall-tools/          P3
│   │   └── src/oncall_tools/
│   │       ├── servers/
│   │       │   ├── telemetry/      __main__.py  server.py  promql.py traces.py
│   │       │   │                                logs.py    changes.py
│   │       │   ├── runbooks_kb/    __main__.py  server.py
│   │       │   ├── tickets/        __main__.py  server.py
│   │       │   └── ops_actions/    __main__.py  server.py   # ONLY write server
│   │       ├── registry.py         # load_tools(transport="stdio"|"http")
│   │       ├── permissions.py      # per-agent tool allow-lists
│   │       ├── hooks/
│   │       │   ├── pre_tool.py     # permissions + call/cost budget + loop breaker
│   │       │   └── post_tool.py    # trace + scrub
│   │       └── guardrails/
│   │           ├── input.py        # telemetry-as-data, secret redaction
│   │           └── output.py       # citation + leak check
│   │
│   ├── oncall-eval/           P4
│   │   └── src/oncall_eval/
│   │       ├── injector.py         # flagd + Toxiproxy fault control
│   │       ├── harness.py          # drives the incident API over HTTP
│   │       ├── checkers.py         # code-first outcome + trajectory checks
│   │       ├── judge.py            # different model family; kappa calibration
│   │       ├── redteam/            # 30+ attacks incl. injection via logs
│   │       └── experiments/        # E1-E4 as config files, not branches
│   │
│   └── oncall-service/        P5
│       └── src/oncall_service/
│           ├── main.py             # app factory; lifespan builds the graph once
│           ├── routes/
│           │   ├── alerts.py       # POST /alerts  (Alertmanager + injector)
│           │   ├── incidents.py    # GET /incidents/{id}[/stream]  (SSE)
│           │   ├── approvals.py    # approve|edit|reject -> Command(resume=...)
│           │   └── health.py
│           ├── schemas/alert.py    # Alertmanager-shaped; one schema, two callers
│           ├── telemetry.py        # prometheus_client + OTel spans for the AGENT
│           ├── config.py           # loads the versioned prompt/model config
│           └── ui/                 # templates/ + static/  (server-rendered + SSE)
│
├── deploy/                    P5
│   ├── compose.oncall.yaml         # ${ONCALL_ROOT}-rooted paths
│   ├── Makefile                    # COMPOSE var; up/stop/down/reset
│   ├── Dockerfile
│   ├── prometheus/rules/checkout.yaml
│   ├── grafana/dashboards/         # agent panels as JSON
│   └── alertmanager.yml            # only if not using Grafana alerting
│
├── data/                      P1   # hand-authored SOURCE corpus, committed
│   ├── runbooks/                   # 15-20, front-matter: service/symptom/fix/preconditions
│   └── postmortems/                # 10 seed
│
├── eval/                      P4   # committed; heldout/ guarded
│   ├── dev/                        # ~20 cases — P2 may read
│   ├── heldout/                    # 40+ cases — P4 only, CODEOWNERS
│   └── faults/manifest.yaml        # fault id -> flag -> root cause -> correct fix
│
├── artifacts/                      # GENERATED, gitignored
│   ├── index/                      # vector + BM25 store
│   ├── runs/<run-id>/              # traces, run records, experiment log
│   └── reports/                    # harness output, promoted into docs/
│
├── docs/
│   ├── architecture.md + diagram.svg
│   ├── threat-model.md        P3
│   ├── memory-policy.md       P1
│   ├── severity-policy.md     P1
│   ├── eval-report.md         P4
│   ├── contribution-log.md    all
│   ├── decisions/                  # dated ADRs
│   ├── superpowers/specs/          # this document
│   └── submission/                 # built PDFs, gitignored
│
├── slides/final-review.pdf         # exported from Google Slides
├── scratch/                        # gitignored
├── vendor/opentelemetry-demo/      # submodule, pinned tag
└── .github/workflows/
```

## 4. Dependency rules

```
oncall-contracts  <-  knowledge, tools, graph, service, eval
knowledge, tools  <-  graph
graph             <-  service
service           <-  eval          (over HTTP, never by import)
eval              <-  nothing
```

Two rules, stated in `CONTRIBUTING.md` and enforced by the guards in Decision 2:

1. **P4's harness drives the system through the incident API**, posting a synthetic alert and
   reading the run record — the same path a real alert takes. Measurement stays at the system
   boundary, and P4 cannot reach inside what P2 is tuning.
2. **Nothing imports `oncall_eval`.** If the agent can import the harness it can reach the
   fault manifest, which contains the correct answer for every case.

`oncall-contracts` exists to break a cycle: if `IncidentState` lived in `oncall-graph`, then
P4 and P5 would both have to import P2's package, and the eval/tuning separation would
collapse. The contracts package is also the literal location of the week-0 contract freeze —
one PR, reviewed by all five.

### Public surfaces

| Package | Public API |
|---|---|
| `oncall-graph` | `build_graph(tools, store) -> CompiledStateGraph` — nothing else |
| `oncall-tools` | `load_tools(transport) -> ToolRegistry`; hook callables |
| `oncall-knowledge` | retriever + memory-store interfaces |
| `oncall-service` | the HTTP API (not a Python API) |
| `oncall-eval` | a CLI; imported by nobody |

Tools reach the graph **by injection, not import**: `build_graph()` receives an already-loaded
toolset. P2 develops against stubs before P3's servers exist, P3 changes server internals
freely, and the per-agent permission split becomes testable — assert that a diagnosis node was
handed a registry containing no tool marked `write`.

### The one P2/P3 seam to settle in week 0

`interrupt()` is a LangGraph primitive, so the call site is in `nodes/remediate.py` (P2), but
the approval payload schema and the permission check are P3's. The payload type lives in
`oncall_contracts.tools`; `remediate.py` only constructs and passes it. Without this agreement
both owners edit the most heavily-graded file in the repo — approval binding to the exact
payload is a live-demo requirement.

## 5. MCP servers: two transports, one entry point

The guide requires stdio in development and HTTP inside Docker, so every server directory has a
`__main__.py` runnable both ways.

- **Dev (stdio):** the graph's process spawns each server as a subprocess —
  `command: uv, args: [run, python, -m, oncall_tools.servers.telemetry]`. Breakpoints work, no
  image rebuilds.
- **Docker (HTTP):** each server is its own container with the same module and
  `--transport http --port 81NN`; the client config points at URLs.

`registry.py` reads the transport from config, so `build_graph(tools=load_tools())` is identical
in both modes and `MCP_TRANSPORT` is the only variable Compose must set.

Port allocation — `8000` for the incident API and `8101–8104` for the four MCP servers. These
are clear of the demo, which claims 8080 (Envoy/frontend), 8081, 8089, 8090, 8013, 8016, 4000,
3000, 9090, 9200, 16686, 4317, and the gRPC service ports.

**Write isolation is enforced in `permissions.py` and `hooks/pre_tool.py`, not by topology.**
In HTTP mode `mcp-ops` is just another container on the Compose network. Those two files
therefore carry a graded requirement and need a test asserting that no diagnosis agent's
registry contains a write tool.

## 6. Written vs generated

`data/` and `eval/` are **authored** and committed. `artifacts/` is **generated** and
gitignored: indexes, traces, run records, the experiment log.

The eval report's numbers are read from `artifacts/runs/` by a make target rather than retyped.
Promotion is one-directional: harness writes `artifacts/`, a target renders into
`docs/submission/`.

## 7. CI gates

| Job | Runs |
|---|---|
| `lint` | ruff + type check across the workspace |
| `test` | per-package pytest, matrix over members — each installs only its own deps |
| `guards` | the five import/leak tests from Decision 2 |
| `eval-gate` | harness on **dev cases only**; fails below threshold |
| `docs` | pandoc build + link check |

**The CI eval gate must never touch held-out cases.** CI runs on every push; pointing it at
held-out would contaminate the set within days and silently invalidate the headline number.
Held-out runs are a manual, release-tagged job that P4 triggers three times, with the playbook
hash recorded in each run record.

The guide requires one failed CI run with an explanation as a deliverable — capture it
deliberately rather than hoping for an accident.

## 8. Verified facts

Established empirically during design; recorded so no one rediscovers them.

### The demo's Compose layout
- The repo uses `compose.yaml`. **There is no `docker-compose.yml`** — that filename 404s.
- Overlays: `compose.yaml` (20 shop services + flagd, load-generator, otel-collector,
  astronomy-db, valkey-cart); `compose.observability.yaml` (**prometheus, grafana, jaeger,
  opensearch**, opamp-server); `compose.full.yaml` (accounting, fraud-detection, **kafka**;
  patches checkout + otel-collector); `compose.extras.yaml` (**intentionally empty** — the
  documented seam for forks).
- **Alertmanager is not shipped**, and `src/prometheus/prometheus-config.yaml` contains no
  `rule_files` and no `alerting` block. Both the rule and the alert routing are ours to build.
  This is very likely why the brief offers "or a Grafana alert" — Grafana is shipped.
- Prometheus runs with `--web.enable-lifecycle`, so `POST /-/reload` applies rule edits without
  a restart.
- **Minimal mode costs a fault family:** `kafka` lives only in `compose.full.yaml`, so ~3 GB
  mode removes `kafkaQueueProblems` plus accounting and fraud-detection as diagnosable
  services. Run full mode on whichever machine can hold it and make that the eval host.

### Compose path resolution across two repos
Relative paths in **all** `-f` files resolve against the **first** file's directory.

| Attempt | Demo's own paths | Our overlay's paths |
|---|---|---|
| default | correct | **silently points into the demo repo** |
| `--project-directory .` | **broken** | correct |
| `${ONCALL_ROOT}`-rooted | correct | correct |

Only env-var-rooted paths keep both valid. This is why `deploy/compose.oncall.yaml` must not
use bare relative paths.

### Stopping the stack
`down` must take the same `-f` list as `up`. Measured on a running 25-container stack: bare
`docker compose down` would remove **20 of 25**, silently orphaning Prometheus, Grafana,
Jaeger, OpenSearch and opamp-server. `deploy/Makefile` holds the file list in one `COMPOSE`
variable so nobody retypes it.

### What `paymentFailure` looks like in metrics
There is **no payment error counter**. `demo_payment_transactions_total` counts only successes,
by currency. The fault appears as a collapse in successes plus **HTTP 422** at the frontend.

Two rules that look correct and detect nothing:
1. Filtering on `http_route="/api/checkout"` returns **no data** — frontend HTTP series carry
   no `http_route` label (only jaeger, cart, shipping, ad populate it).
2. Using all frontend traffic as the denominator measures **0.5%**, because the load generator
   browses far more than it checks out; a `> 5%` threshold never fires even with payments
   fully down.

The working expression pairs 422s with payment successes, reads `1.0` with the fault on, and
yields `NaN` on an idle shop so it cannot fire spuriously:

```promql
sum(rate(http_server_request_duration_seconds_count{
      service_name="frontend", http_response_status_code="422"}[5m]))
/ ( sum(rate(http_server_request_duration_seconds_count{
        service_name="frontend", http_response_status_code="422"}[5m]))
    + sum(rate(demo_payment_transactions_total[5m])) ) > 0.05
```

`checkout`'s `rpc_client` series carry no `rpc_grpc_status_code` label, so gRPC failures are
invisible in metrics. **Metrics say checkout is broken; only traces say payment is why.** The
`telemetry` tool must therefore return correlated metric *and* trace evidence, or the diagnosis
agents cannot localise a fault to a service — which is most of signature challenge A.

## 8a. Experiment E1 is a structural requirement, not a reporting step

E1 compares C0 (fixed workflow), C1 (one agent, all tools) and C2 (the team) on
the same model, tools and budget. Three consequences for structure:

1. **`build_graph` takes a keyword-only `topology`.** All three configurations must
   stay runnable for the life of the project, so C1 is grown into C2 by *adding* a
   topology, never by mutating one. C1 is written first — it is the week-1 vertical
   slice — but as `topologies/c1_single.py`, not as "the graph".
2. **C1 and C2 share `nodes/` and `planning/`.** If each carried its own hypothesis
   ranking, E1 would measure node quality rather than topology.
3. **Every run record carries `config_id` and `complexity`.** Any claim of the form
   "the team helps on complex multi-service incidents but adds overhead on simple
   ones" is conditional, and an aggregate number cannot support it. `complexity` is
   derived from `len(root_cause_services)`, and `flags` is a list so the
   two-faults-at-once cases the guide requires are expressible.

A caution that belongs with the design: this hypothesis is falsifiable. The team may
prove worse everywhere, or better everywhere, and with ~20 development cases split
across two strata a per-stratum difference can easily be noise. Report what the runs
show, including when n is too small to conclude.

## 9. Out of scope

- No `src/` at the repo root; workspace members own their own `src/`.
- No per-owner top-level directories — owners map to packages.
- No committed notebooks as deliverables; `scratch/` is gitignored.
- Agent behavioural design (prompt text, hypothesis ranking, lesson schema) belongs in
  per-component specs.
- Kubernetes or cloud deployment. Docker Compose on a laptop is the target, per the guide.

## 10. Open questions

1. **Team size** — instructor confirmation that five is permitted (Decision 0).
2. **Demo submodule pin** — which tag. The running stack reports `service_version 3.1.0`;
   confirm the matching git tag before pinning.
3. **Alert routing** — Grafana alerting (no new container, sanctioned by the brief) versus
   adding Alertmanager. Recommend Grafana given the 6 GB ceiling; `deploy/alertmanager.yml`
   is listed conditionally.
4. **Existing demo clone** — `~/work/Projects/opentelemetry-demo` sits outside
   `vendor/`. Re-clone as a submodule or point at it; either way `make up` needs the
   `vendor/opentelemetry-demo/` prefix.
