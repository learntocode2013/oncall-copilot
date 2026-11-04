# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Agentic AI Capstone, Project 4 — "On-Call Copilot": an agent that reads real telemetry from a
running shop, works out what failed, proposes **one** allow-listed fix for a human to approve,
verifies it, and writes the incident up. The system under test is the OpenTelemetry Astronomy
Shop, vendored as a pinned submodule; its `flagd` feature flags inject the faults, which is why
ground truth is free and the fault injector is a first-class component rather than test
scaffolding.

---

## How to work on this project — read this before writing anything

**This capstone is co-built, not generated.** There is an individual viva: each of five owners
must redraw the architecture from memory and defend their own component. The course guide allows
AI tools to write parts of the implementation, but requires the problem framing, design,
evaluation and interpretation to be the team's own.

What that means in practice:

- **Explain the decision space; the owner decides.** Lay out the options and the trade-offs.
  Never pick a design for them and present it as settled.
- **Write the files yourself once the design is agreed** — the owner's standing instruction from
  2026-11-04. Show the code, write it to disk, and let them review `git diff`. This replaced
  hand-pasting from chat, which silently mangled three files: one paste lost a class-body indent
  level so every method became module-level, another dropped a whole function signature and part
  of a comprehension. The co-build rule protects authorship of *decisions*, not of retyping.
- **Do not produce large artifacts unprompted.** No speculative modules, docs or refactors.
- **Mechanical plumbing is yours:** Compose, Makefile, CI, scaffolding, build files — nobody is
  examined on those. Graded content (runbooks, policies, the graph, the eval harness,
  architecture docs) is co-written at the owner's pace.
- **Never run the plan autonomously.** `docs/superpowers/plans/` is a reference the owner
  executes task by task — not a script to execute. Do not use subagent-driven development,
  parallel agent dispatch, or autonomous plan execution on this repository.

When unsure whether something is plumbing or graded work, ask.

---

## Commands

```bash
# Python
make test                 # whole suite (pytest -q)
make lint                 # ruff check + format --check — READ-ONLY, reports nothing changed
make fix                  # apply the safe fixes, then re-run lint
make guards               # just tests/test_guards.py, verbose

# one test / one file / by name
uv run pytest tests/test_guards.py::test_corpus_validates -v
uv run pytest packages/oncall-knowledge/tests/test_validator.py -v
uv run pytest -k corpus -v

# validate the runbook corpus directly
uv run python -m oncall_knowledge.validator

# the shop
make bootstrap            # the one post-clone command; idempotent, re-run any time
make up / stop / down     # start / pause / remove (stop+up resumes in seconds)
make reset                # also WIPES Prometheus + OpenSearch data — untested so far
make verify / urls        # health checks / where everything is
make config               # render the merged compose config (read this after overlay edits)
make reload-rules         # POST /-/reload to Prometheus
```

`MODE=full` (default) or `MODE=minimal`; set it once per machine in `deploy/local.mk`
(gitignored). Minimal drops `accounting`, `fraud-detection` and `kafka` — and with kafka goes the
`kafkaQueueProblems` fault family. **Development on minimal is fine; evaluation must run on
full**, or scored runs and tuning runs measure different systems.

**After `git pull`, run `make bootstrap`, not `make up`** — `up` does not run `uv sync` and does
not update the submodule.

---

## Architecture

A uv workspace, one package per architectural layer, with the dependency direction enforced by a
test rather than by convention:

```
oncall-contracts  ◀──  knowledge, tools, graph, service, eval
knowledge, tools  ◀──  graph
graph             ◀──  service
service           ◀──  eval          (over HTTP, never by import)
eval              ◀──  nothing
```

`oncall-contracts` exists only to break a cycle: if `IncidentState` lived in `oncall-graph`, both
P4 and P5 would import P2's package and the eval/tuning separation would collapse.

Three rules decide the design, and they are the reason policy lives in Python instead of prompts:

1. **The model proposes; code and people decide.** Severity thresholds, allow-lists, budgets and
   rollback logic are plain tested Python. The LLM picks which rule applies and explains it.
2. **Outside text is data, never instructions.** The attack surface *is* the telemetry we read.
3. **A person approves each exact change.** Approval binds to the exact payload; any edit
   invalidates it.

And one hard prohibition: **the agent never gets a shell.** No `bash`, no `docker exec` — named,
typed, allow-listed actions only.

**Experiment E1 is structural, not a reporting step.** `build_graph` takes a keyword-only
`topology`: `c0_workflow`, `c1_single` or `c2_team`, living side by side in
`oncall_graph/topologies/`. **Grow C1 into C2 by adding a topology, never by mutating one** — all
three must stay runnable for the life of the project. C1 and C2 share `nodes/` and `planning/`,
or E1 measures node quality instead of topology. Every run writes a `RunRecord` carrying both
`config_id` and `complexity`; without both, the only available claim is one aggregate number.

### Ownership

| | Area | Owns |
|---|---|---|
| P1 | Domain, knowledge & memory | `packages/oncall-knowledge/`, `data/` |
| P2 | Orchestration, planning & improvement | `packages/oncall-graph/` |
| P3 | Tools, human control & security | `packages/oncall-tools/` |
| P4 | Evaluation & red team | `packages/oncall-eval/`, `eval/` |
| P5 | Deployment & observability | `packages/oncall-service/`, `deploy/` |

P4's role cannot be merged into anyone else's: the person tuning the agent must not write the
held-out cases, and the person building the defences must not write the attacks.

### State of play

`oncall-contracts`, `oncall-knowledge` and `oncall-graph` have code. **`oncall-eval`,
`oncall-service` and `oncall-tools` are empty directories with no `pyproject.toml`** — P3, P4 and
P5 have nowhere to put code until those exist. The incident API, the four MCP servers, the fault
injector and the eval harness are all unbuilt. 16 of the 18 runbooks are `status: structural`
(behaviour never actually observed); only the two payment faults are `verified`.

---

## Why `tests/test_guards.py` exists, and why you must not weaken it

**Every failure this project has hit so far looked like success.** An invalid flagd variant never
fires and the run looks healthy. A stalled request emits no status code and the error rate reads
zero. A query copied into its own document matches itself and Recall@5 goes up. Contamination of
the held-out set raises nothing. A fault left enabled in a committed file looks like a clean repo.
None of those produce an error, so none can be caught by watching for errors.

The guards encode graded requirements — contamination isolation, dependency direction, one source
of truth for severity, the committed-flag baseline. **If a guard fails, it is reporting a real
leak: fix the code, not the guard.** Pending guards `pytest.skip` with a reason naming what will
activate them, because a guard that silently passed for want of a subject would be worse than
none.

---

## Gotchas that have already cost us time

**Flag variants in YAML must be quoted.** `variant: off` loads as the boolean `False`, which
invalidated 27 actions in one migration. Quote every arg value unconditionally. And a flagd
variant that does not exist produces no error — flagd just ignores it and the run looks healthy.

**Never commit a fault left enabled in `deploy/flagd/demo.flagd.json`.** `check_flag_baseline`
guards this now, because a repo with a live fault in it looks clean.

**Severity comes from observed customer impact** — never from container health, and never from
error rate alone. See `docs/severity-policy.md`.

**Memory stores references to evidence, not evidence itself**, with the status vocabulary
`observed | inferred | approved | rejected`. See `docs/memory-policy.md`.

**pytest runs in `--import-mode=importlib`.** Two test files with the same basename in sibling
packages abort the *whole* run with "import file mismatch" — not just that file. An `__init__.py`
in `tests/` does not fix it. Keep test basenames unique anyway.

**ruff excludes `vendor/`** — otherwise a clean clone reports 118 errors in someone else's code.
Unfixable rules (`TRY004`, `BLE001` in the validator) need a `# noqa` **with a reason**; a bare
`noqa` is silencing, not a decision.

**Compose: relative paths in every `-f` file resolve against the *first* file's directory**,
which is the demo repo. Our overlay must use `${ONCALL_ROOT}`-rooted paths. `--project-directory`
cannot fix both repos at once. Run `make config` and read the output after any overlay change.

**`down` must take the same `-f` list as `up`.** A bare `docker compose down` removed 20 of 25
containers and silently orphaned Prometheus, Grafana, Jaeger and OpenSearch.

**Go services need `GOMEMLIMIT` headroom.** It is a *soft* limit: set too low, the service enters
a GC death spiral — pinned CPU, never binds its port, `OOMKilled: false`, `RestartCount: 0`, so
nothing looks like an OOM. See `docs/decisions/0002-*`.

**Adding a workspace package needs three things**, not one: the member directory, its own
`[build-system]` (hatchling, src-layout as `src/<name_with_underscores>/`), and
`[tool.uv.sources] <name> = { workspace = true }` in the root `pyproject.toml`.

**Ports:** incident API `8000`, MCP servers `8101`–`8104`. The demo claims 8080, 8081, 8089,
8090, 8013, 8016, 4000, 3000, 9090, 9200, 16686 and 4317.

---

## Git and CI

`main` requires four passing checks — `lint`, `test`, `guards`, `lockfile` — with admin
enforcement and "branch must be up to date". So:

- **Everything lands through a PR.** No direct pushes to `main`, including by the repo owner.
- **Rebase onto `main` before merging.** A merge rejected for being out of date is not a check
  failure.
- **Cut feature branches from `main`**, freshly fetched. A branch accidentally cut from another
  feature branch carries that branch's commits into your PR.
- **Stage explicitly; never `git add -A`.** `.idea/` has been swept into a commit this way.
- CI runs on every branch push, not just `main`, so a broken guard surfaces at the commit that
  introduced it rather than three commits later at PR time.
- Commit messages say *why*, not just what. If a commit encodes a decision, add a dated ADR under
  `docs/decisions/` and reference it.

---

## Where to read more

| Read | For |
|---|---|
| `CONTRIBUTING.md` | The rules CI enforces, public surfaces per package, the P2/P3 seam, MCP transports |
| `docs/superpowers/specs/2026-10-24-oncall-copilot-repo-structure-design.md` | The four decisions, dependency rules, and §8 **Verified facts** — what we established by running things rather than assuming |
| `docs/superpowers/plans/2026-10-24-oncall-copilot-repo-scaffold.md` | The nine scaffold tasks (owner-executed; see the rule at the top of this file) |
| `docs/decisions/` | Dated ADRs, each with a reproduction and measurements |
| `docs/severity-policy.md`, `docs/memory-policy.md` | P1's graded policy pages |
| `docs/architecture.md`, `docs/architecture.mmd` | The component diagram and its allowed edges |
