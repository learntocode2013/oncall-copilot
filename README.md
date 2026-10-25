# On-Call Copilot

Agentic incident response for a running online shop. When an alert fires, the agent uses real
telemetry to work out what failed and why, proposes **one** safe fix for an engineer to approve,
verifies that it worked, and writes the incident up.

Agentic AI Capstone — Project 4 (IT operations, "production" family). Team of 5.

---

## Status: design complete, scaffold not yet built

Right now this repo contains **two documents and no code**. Read them in this order:

| Read | What it gives you |
|---|---|
| `docs/superpowers/specs/2026-10-24-oncall-copilot-repo-structure-design.md` | Why the repo is shaped the way it is — four decisions, the dependency rules, and a **Verified facts** section recording what we established by running things rather than assuming |
| `docs/superpowers/plans/2026-10-24-oncall-copilot-repo-scaffold.md` | Nine step-by-step tasks that build the scaffold. Someone executes this before anyone starts on their own component |

Everything below describes the repo **as it will be once the plan's nine tasks are done**.
Commands marked 🚧 do not work yet.

---

## What we are building

```
alert ──▶ incident API ──▶ incident commander (LangGraph)
                                  │
            ┌─────────────────────┼──────────────────────┐
            ▼                     ▼                      ▼
         triage          diagnosis agents           runbook agent
                        (parallel, one per        (+ past incidents,
                         suspect service)          precondition-checked)
                                  │
                                  ▼
                       proposed action (ONE, allow-listed)
                                  │
                                  ▼
                   ⚠ approval gate — a human approves the exact command
                                  │
                                  ▼
                     remediate ──▶ verify ──▶ roll back if it did not help
                                  │
                                  ▼
                        status note + postmortem + lessons
```

The system under test is the **OpenTelemetry Astronomy Shop**, vendored as a pinned submodule.
Its `flagd` feature flags inject the faults — which means *we* know the true root cause and the
correct fix for every test case, with no hand-labelling. That is this project's single biggest
advantage, and it is why the fault injector is a first-class component rather than test
scaffolding.

### Three rules that decide the architecture

1. **The model proposes; code and people decide.** Severity thresholds, allow-lists, budgets and
   rollback logic live in plain, tested Python. The LLM picks which rule applies and explains it.
2. **Outside text is data, never instructions.** Log lines, trace attributes and runbook text can
   carry attacks. Our attack surface *is* the telemetry we read.
3. **A person approves each exact change.** Approval binds to the exact payload. Any edit
   invalidates the earlier approval.

And one hard prohibition: **the agent never gets a shell.** No `bash`, no `docker exec`. Named,
typed, allow-listed actions only.

---

## Getting started 🚧

> **These commands do not work yet.** There is no `Makefile` in the repo — `make bootstrap`
> will fail with *"No rule to make target"*. The scaffold is Task 6 of
> `docs/superpowers/plans/2026-10-24-oncall-copilot-repo-scaffold.md` and has not been run.
> This section describes the intended workflow; it goes live when that plan is executed.

```bash
git clone --recurse-submodules git@github.com:learntocode2013/oncall-copilot.git
cd oncall-copilot && make bootstrap
```

That is the whole setup. `make bootstrap` initialises the submodule, creates `.env` from the
example, runs `uv sync`, starts the shop with its telemetry backends plus our four MCP servers
and the incident API, waits until they are healthy, then checks three endpoints and prints the
URLs below.

**It will stop once, on purpose.** `OPENROUTER_API_KEY` is the one thing that cannot be
committed, so the first run on a clean machine halts with a single instruction: add the key to
`.env`. Re-run `make bootstrap` and it completes.

`make bootstrap` is **idempotent** — re-run it whenever you want. It never clobbers an existing
`.env`, and it repairs a clone made without `--recurse-submodules`, so forgetting that flag
costs you nothing.

First run pulls and builds roughly 25 images. If it exceeds the 600-second health-wait, just run
it again: everything is cached and the second pass is quick.

Needs roughly **6 GB RAM** and **14 GB disk**, plus Docker Compose v2. If your laptop cannot
hold it, the sanctioned fallback is a four-service FastAPI shop with faults injected via
Toxiproxy — but raise it in week 0, because it changes what the telemetry tools talk to.

### Where things are

| Surface | URL |
|---|---|
| Incident UI (ours) | http://localhost:8000 |
| Web store | http://localhost:8080/ |
| Grafana | http://localhost:8080/grafana/ |
| Jaeger | http://localhost:8080/jaeger/ui/ |
| **Feature flags** (your fault switch) | http://localhost:8080/feature |
| Prometheus | http://localhost:9090 |

### Your first five minutes with the stack

Open the feature-flag UI, switch `paymentFailure` **on**, and watch Prometheus:

```promql
sum(rate(http_server_request_duration_seconds_count{service_name="frontend",
      http_response_status_code="422"}[5m]))
sum(rate(demo_payment_transactions_total[5m]))
```

The first climbs, the second drops to zero. That one manual loop proves the stack, the fault
mechanism and the observability path all work. Switch the flag back off and watch it recover.

Two things worth knowing before you write a query of your own, both verified against a running
stack: **there is no payment error counter** (`demo_payment_transactions_total` counts only
successes), and the frontend's HTTP series carry **no `http_route` label**, so a rule scoped to
`http_route="/api/checkout"` silently returns no data. See the spec's Verified facts section.

---

## Commands 🚧

| Command | What it does |
|---|---|
| `make bootstrap` | **The one post-clone command.** Submodule, `.env`, `uv sync`, start, health-wait, URLs. Idempotent |
| `make up` | Start everything, assuming bootstrap already ran once |
| `make verify` | Curl the three health endpoints — our clean-machine install evidence |
| `make urls` | Print the URL table |
| `make stop` | Halt containers, keep data — `make up` resumes in seconds |
| `make down` | Remove containers, keep volumes |
| `make reset` | **Also wipes Prometheus/OpenSearch data.** Use between eval runs; never before a demo |
| `make config` | Render the merged Compose config — how you check an overlay change took effect |
| `make reload-rules` | Apply edited Prometheus alert rules without a restart |
| `make test` | Whole test suite |
| `make guards` | Just the structural guards (see below) |
| `make lint` | ruff check + format check |
| `make docs` | Build the submission PDFs |

**`down` must take the same `-f` list as `up`.** A bare `docker compose down` orphans 5 of 25
containers — your whole telemetry backend keeps running. That is why the file list lives in one
`COMPOSE` variable in `deploy/Makefile` and you should always go through `make`.

---

## Layout

```
packages/            one uv workspace member per owner
  oncall-contracts/    shared types — depends on NOTHING
  oncall-knowledge/  P1   ingestion, hybrid retrieval, memory
  oncall-graph/      P2   incident commander, specialists, planning, improvement
  oncall-tools/      P3   4 MCP servers, hooks, approval gate, guardrails
  oncall-eval/       P4   fault injector, harness, judge, red team
  oncall-service/    P5   incident API, UI, metrics
deploy/            P5   Compose overlay, Dockerfile, Makefile, alert rules, dashboards
data/              P1   AUTHORED corpus: runbooks + postmortems (committed)
eval/              P4   dev/ (~20 cases), heldout/ (40+, P4 only), faults/manifest.yaml
artifacts/              GENERATED: indexes, traces, run records (gitignored)
docs/                   threat model, memory policy, eval report, ADRs, specs, plans
vendor/                 the OTel demo, pinned submodule
```

`data/` and `eval/` are **written by hand**. `artifacts/` is **generated** and gitignored — and
it is the only source of numbers for the eval report. Nobody retypes a figure into a document.

---

## Who owns what

Fill in names at your first meeting and keep them here.

| | Name | Area | Lectures | Lives in |
|---|---|---|---|---|
| **P1** | _____ | Domain, knowledge & memory | L4–L6, L9 | `packages/oncall-knowledge/`, `data/` |
| **P2** | _____ | Orchestration, planning & improvement | L8, L10–L12 | `packages/oncall-graph/` |
| **P3** | _____ | Tools, human control & security | L7, L14, L15 | `packages/oncall-tools/` |
| **P4** | _____ | Evaluation & red team | L13, L15 | `packages/oncall-eval/`, `eval/` |
| **P5** | _____ | Deployment & observability | L13, L16 | `packages/oncall-service/`, `deploy/` |

The course guide defines **six** roles and we are five. We merged roles 1 and 2 (domain lead +
knowledge engineer) because that is the one pairing where the work is genuinely the same
pipeline — the domain lead writes the runbooks the knowledge engineer ingests. **P4 cannot be
merged into anything**; see the separations below.

> **Open item:** confirm with the instructor that a team of five is permitted. Team rules come
> from the cohort's separate *Capstone Project Guidelines*.

---

## How to contribute

### The flow

```bash
git switch -c p3/approval-gate        # <owner>/<topic>
# ... work, committing as you go ...
make lint && make test                # must be green before you push
git push -u origin p3/approval-gate
# open a PR — CODEOWNERS routes review to the owner of each path you touched
```

- **Branch per piece of work**, named `p<N>/<topic>`. Never commit to `main`.
- **Small, frequent commits.** Say *why* in the message, not just what.
- **One reviewer minimum**, and CODEOWNERS decides who. If you touched someone else's package,
  they review it.
- **CI must be green.** The `guards` job is not allowed to fail (see below).
- **Log your work** in `docs/contribution-log.md` as you go, not the week before submission.
  It is a graded artifact and reconstructing it from memory shows.

### Two separations that affect your marks

These are not etiquette. The marking relies on them.

1. **The person tuning the agent (P2) must not write the held-out cases (P4).**
2. **The person building the defences (P3) must not write the attacks (P4).**

Both land on P4, which is why P4's role cannot be merged into anyone else's and why
`eval/heldout/` is P4's alone under CODEOWNERS.

### Four import rules, enforced by `make guards`

1. **Nothing imports `oncall_eval`.** If the agent can import the harness it can reach the fault
   manifest, which holds the correct answer for every case.
2. **`oncall-eval` drives the system over HTTP**, never by importing `oncall_graph`. Measurement
   stays at the system boundary, and P4 cannot reach inside what P2 is tuning.
3. **Shared types live in `oncall-contracts`**, which imports nothing from the workspace.
4. **Tools reach the graph by injection**, not import — `build_graph(tools, store, *, topology)` receives an
   already-loaded toolset, which is what keeps a diagnosis node's tool list assertable.

Why tests and not a style guide: **contamination of the held-out set makes your scores go *up*
and raises no error.** An ingestion glob that quietly sweeps in the answers produces a better
number with no stack trace and nothing to notice — you would find out at the viva. A guard that
fails the build is the only thing that catches it.

### Working in the uv workspace

```bash
uv sync                                  # whole workspace, one lock file
uv add --package oncall-tools httpx      # add a dep to YOUR package only
uv run --package oncall-graph pytest packages/oncall-graph/tests -v
```

Each member needs its own `[build-system]` (hatchling) **and** its own
`[tool.uv.sources] <sibling> = { workspace = true }` for every sibling it depends on.
Declaring sources only at the root does not work — verified on uv 0.11.17.

Add a dependency to **your** package's `pyproject.toml`, not the root. That is the whole point of
the workspace split: P5's service job should not install P1's reranker.

---

## Your first week, by owner

Agree the six **week-0 contracts** before anyone writes a node — they are what lets five people
work in parallel against stubs. One PR into `oncall-contracts`, reviewed by all five.

| Contract | Owner | Unblocks |
|---|---|---|
| `IncidentState` — alert, scope, hypotheses, evidence, plan, action, verification | P2 | everyone reads/writes state |
| MCP tool schemas for all four servers, each marked read or write | P3 | P2 builds against stubs; P4 asserts on tool names |
| Fault manifest — fault id → flag → true root cause → correct action | P4 | the ground truth everything scores against |
| Run-record JSON written per incident | P5 | P4 scores trajectories; dashboards read the same fields |
| Runbook front matter — service, symptom, fix, preconditions | P1 | retrieval metadata and lesson shape at once |
| Model IDs pinned in one config | P5 | comparable experiments |

Then:

- **P1** — write the first 3 runbooks with real front matter; get ingestion indexing `data/` only.
- **P2** — the graph skeleton: commander plan, one node, typed state, checkpointer, stop/resume.
- **P3** — the `telemetry` server with one working PromQL tool, marked read.
- **P4** — the fault injector flipping `paymentFailure` and asserting the detection query moves.
- **P5** — the stack up on every machine that can hold it, plus `POST /alerts` → `GET /stream`.

The week-1 target is **one honest vertical slice**: `paymentFailure` → one PromQL tool → one
agent → proposed `set_flag` → human approval → verified recovery. Ugly but end to end.

### Two ordering mistakes that cost teams marks

- **Do not build the fault injector last.** It is the ground truth — without it P2 has nothing to
  tune against and P4 nothing to score.
- **Do not touch held-out cases before the playbook is frozen.** If you change anything after
  seeing held-out results, those cases become development cases and you must say so in the report.

---

## What everyone must be able to do at the viva

The viva is individual, and everyone is asked the same opening question: **redraw the
architecture from memory and explain your own part in depth.** Also be ready for:

- Trace one request through the diagram — which boxes did it pass through, and what did each decide?
- Which decisions were the model's, and which were code's, rules' or a person's?
- What did the team of agents buy over the single-agent baseline, and at what cost?
- Which experiment changed the design?
- Show the traces for missing evidence, a failed tool, and an injected instruction.
- What does memory keep, why, and how is it deleted?
- What did the improvement loop learn, and how do you know it helps on unseen cases?
- What would be needed before a supervised pilot with real users?

AI coding tools may write parts of the implementation. **The problem framing, design, evaluation
and interpretation must be ours.**

---

## Documents

| Document | Owner |
|---|---|
| `docs/architecture.md` + diagram | P1 |
| `docs/threat-model.md` | P3 |
| `docs/memory-policy.md` | P1 |
| `docs/severity-policy.md` | P1 |
| `docs/eval-report.md` (4–6 pp) | P4 |
| `docs/contribution-log.md` | everyone, from day one |
| `docs/decisions/` — dated ADRs | whoever makes the call |
| `slides/final-review.pdf` | P1 + P5 |

Of these, only the spec and plan under `docs/superpowers/` exist today — the rest are created
as stubs with owner lines by the last task of the scaffold plan.

Prose is markdown so it diffs and merges like code; `make docs` renders the submission PDFs.
Slides are authored in Google Slides and only the exported PDF is committed.

**Never commit:** API keys, `.env`, private data, or anything we cannot redistribute.
