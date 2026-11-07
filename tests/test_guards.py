"""Structural guards. These encode graded requirements, not style preferences.

Two kinds of test live here:

* **Active** — the thing it guards exists, so the guard enforces it now.
* **Pending** — the subject does not exist yet, so the test SKIPS with a reason
  naming what will activate it. A skip is visible in `-v` output; a guard that
  silently passed because its subject was missing would not be.

The reason this file exists at all: every failure this project has hit so far
looked like success. An invalid flagd variant never fires and the run looks
healthy. A stalled request emits no status code and the error rate reads zero.
A query copied into its own document matches itself and Recall@5 goes up.
Contamination of the held-out set raises nothing. None of those produce an
error, so none of them can be caught by watching for errors.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "packages"
EVAL_PKG = "oncall-eval"

# Any reference to the held-out directory, however spelled. Keyed on the PATH
# rather than the bare word, so prose discussing held-out methodology does not
# break the build while `eval/held_out/` cannot evade the guard.
HELDOUT_PATH = re.compile(r"""eval[/\\]held[_-]?out|["']held[_-]?out["']""", re.IGNORECASE)

# The dependency graph from the repo structure design, as module names.
# oncall-contracts depends on nothing inside the workspace; that asymmetry is
# what lets the evaluation harness read run records without importing the
# orchestration package.
ALLOWED_IMPORTS: dict[str, set[str]] = {
    "oncall_contracts": set(),
    "oncall_knowledge": {"oncall_contracts"},
    # tools MAY import knowledge: mcp-runbooks is a thin wrapper over Retriever,
    # which is what "retrieval.py is the only public surface" was for. See the
    # ⚠ note in CONTRIBUTING.md -- the rules previously forbade the package that
    # needed retrieval and permitted the one that should not reach for it.
    "oncall_tools": {"oncall_contracts", "oncall_knowledge"},
    # graph may NOT. P2 reaches retrieval through mcp-runbooks only. Without
    # this, one E1 topology could retrieve through MCP while another imported
    # directly, and the comparison between them would differ in retrieval as
    # well as orchestration -- the one thing E1 exists to isolate.
    "oncall_graph": {"oncall_contracts", "oncall_tools"},
    # Memory is P1's, and the graph may not import it either -- for the same
    # reason it may not import knowledge. The graph receives a store OBJECT and
    # calls `search(service, symptom, status)` on it, which needs no import, so
    # `load_lessons` works against a list in a test and against the real
    # playbook in a run. The SERVICE constructs it, which is why the service may
    # import memory and the graph may not.
    "oncall_memory": {"oncall_contracts", "oncall_knowledge"},
    "oncall_service": {"oncall_contracts", "oncall_graph", "oncall_memory"},
    "oncall_eval": {"oncall_contracts"},
    # The lab holds the fault manifest's root cause. NOTHING MAY IMPORT THE LAB
    # -- that is the rule, and it is unchanged. The lab importing contracts is
    # the other direction: it reads the shared flagd writer from
    # oncall_contracts.flagd, which ops_actions also uses with the opposite
    # intent. contracts imports nothing, so there is no cycle.
    "oncall_lab": {"oncall_contracts"},
}


def dist_to_module(name: str) -> str:
    return name.replace("-", "_")


def real_packages() -> list[Path]:
    """Package directories that actually contain a pyproject.toml."""
    return sorted(p for p in PACKAGES.iterdir() if (p / "pyproject.toml").is_file())


def python_files(exclude_package: str | None = None):
    for path in PACKAGES.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(PACKAGES)
        if exclude_package and rel.parts[0] == exclude_package:
            continue
        yield path


# ---------------------------------------------------------------- active ----


def test_corpus_validates() -> None:
    """The runbook corpus must validate. This is the guard with the most reach.

    It checks every flagd variant against the real flag definitions, that
    executable fixes carry a rollback, that symptom runbooks declare no fix,
    that a symptom's candidates resolve to real cause runbooks, and that no
    labelled query appears verbatim in its own document body.

    It does NOT check that any runbook is factually correct. Most of the corpus
    is `status: structural` — behaviour never observed — and all of it passes.
    """
    from oncall_knowledge.validator import validate_corpus

    documents, issues = validate_corpus(
        ROOT / "data" / "runbooks",
        ROOT / "deploy" / "flagd" / "demo.flagd.json",
        ROOT / "eval" / "faults" / "manifest.yaml",
    )
    errors = [i for i in issues if i.severity == "error"]
    assert documents, "no runbooks found"
    assert not errors, "corpus validation failed:\n" + "\n".join(str(i) for i in errors)


def test_no_fault_is_enabled_in_the_committed_flag_baseline() -> None:
    """deploy/flagd is version-controlled AND mounted into flagd.

    A fault left enabled here ships a broken shop to everyone who pulls. This
    has already happened once: an experiment left paymentFailure at "100%" in
    a tracked file. Faults are injected at run time, never committed.
    """
    from oncall_knowledge.validator import check_flag_baseline

    issues = check_flag_baseline(ROOT / "deploy" / "flagd" / "demo.flagd.json")
    assert not issues, "\n".join(str(i) for i in issues)


def test_severity_has_one_source_of_truth() -> None:
    """The corpus validator must not keep its own copy of the severity values."""
    from typing import get_args

    from oncall_contracts.config import Severity
    from oncall_knowledge.schema import SEVERITIES

    assert SEVERITIES == frozenset(get_args(Severity))


@pytest.mark.parametrize("pkg", [p.name for p in real_packages()])
def test_dependency_direction(pkg: str) -> None:
    """A package may only import the workspace packages the design allows.

    Checked against actual imports, not just declared dependencies, because an
    import that works by accident of a transitive install is still a violation.
    """
    module = dist_to_module(pkg)
    allowed = ALLOWED_IMPORTS[module] | {module}
    pattern = re.compile(r"^\s*(?:from|import)\s+(oncall_\w+)", re.MULTILINE)

    offenders: list[str] = []
    for path in (PACKAGES / pkg).rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for imported in pattern.findall(path.read_text(encoding="utf-8")):
            if imported not in allowed:
                offenders.append(f"{path.relative_to(ROOT)} imports {imported}")
    assert not offenders, f"{module} may only import {sorted(allowed - {module})}:\n" + "\n".join(
        offenders
    )


@pytest.mark.parametrize("pkg", [p.name for p in real_packages()])
def test_package_is_registered_in_the_workspace(pkg: str) -> None:
    """A package with a pyproject must be a workspace member, or nothing can
    import it.

    oncall-knowledge was written, committed and unusable for exactly this
    reason: absent from the root dependencies, so `uv sync` never installed it.
    """
    root = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = {d.split(">")[0].split("=")[0].strip() for d in root["project"]["dependencies"]}
    sources = set(root.get("tool", {}).get("uv", {}).get("sources", {}))
    assert pkg in deps, f"{pkg} is not in the root [project] dependencies"
    assert pkg in sources, f"{pkg} is not in the root [tool.uv.sources]"


def test_nothing_imports_oncall_eval() -> None:
    """If the agent can import the harness it can reach the fault manifest,
    which holds the correct answer for every case."""
    offenders = [
        str(p.relative_to(ROOT))
        for p in python_files(exclude_package=EVAL_PKG)
        if re.search(
            r"^\s*(?:from|import)\s+oncall_eval\b", p.read_text(encoding="utf-8"), re.MULTILINE
        )
    ]
    assert not offenders, f"these files import oncall_eval: {offenders}"


def test_nothing_imports_oncall_lab() -> None:
    """The lab shows the operator the root cause. The agent must not import it."""
    offenders = [
        str(p.relative_to(ROOT))
        for p in python_files(exclude_package="oncall-lab")
        if re.search(
            r"^\s*(?:from|import)\s+oncall_lab\b", p.read_text(encoding="utf-8"), re.MULTILINE
        )
    ]
    assert not offenders, f"these files import oncall_lab: {offenders}"


def test_heldout_never_referenced_outside_eval() -> None:
    """Contamination of the held-out set makes scores go UP and raises nothing."""
    offenders = [
        str(p.relative_to(ROOT))
        for p in python_files(exclude_package=EVAL_PKG)
        if HELDOUT_PATH.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"held-out path referenced outside oncall-eval: {offenders}"


# --------------------------------------------------------------- pending ----


def test_eval_does_not_import_graph() -> None:
    """P4 drives the system over HTTP so measurement stays at the system
    boundary and P4 cannot reach inside what P2 is tuning."""
    eval_src = PACKAGES / EVAL_PKG / "src"
    if not eval_src.is_dir():
        pytest.skip("activates when packages/oncall-eval has source")
    offenders = [
        str(p.relative_to(ROOT))
        for p in eval_src.rglob("*.py")
        if re.search(
            r"^\s*(?:from|import)\s+oncall_graph\b", p.read_text(encoding="utf-8"), re.MULTILINE
        )
    ]
    assert not offenders, f"oncall-eval must not import oncall_graph: {offenders}"


# RETIRED: test_ingestion_globs_exclude_the_eval_tree.
#
# It importorskip'd `oncall_knowledge.ingest` for an `ingestion_globs()` that was
# never written -- the module landed as `oncall_knowledge.ingestion`, exposing
# `ingest_runbooks()` with the corpus root pinned instead. So the guard skipped
# on every run and would have forever, which is worse than absent: a permanent
# skip reads as pending coverage in -v output and provides none.
#
# Its property is now covered more directly by
# test_every_index_entry_comes_from_the_data_tree, which asserts on the
# source_path of what actually got indexed rather than on a glob pattern that
# might not be the one ingestion uses.


def test_every_package_has_a_codeowner() -> None:
    """A package added with no owner silently drops the review separations the
    marking relies on: the person tuning the agent must not write the held-out
    cases, and the person building the defences must not write the attacks."""
    codeowners = ROOT / "CODEOWNERS"
    if not codeowners.is_file():
        pytest.skip("activates when CODEOWNERS exists (needs the team's GitHub handles)")
    owners = codeowners.read_text(encoding="utf-8")
    missing = [
        d.name
        for d in sorted(PACKAGES.iterdir())
        if d.is_dir() and f"packages/{d.name}/" not in owners
    ]
    assert not missing, f"packages with no CODEOWNERS entry: {missing}"


#: Servers that must serve nothing but reads. `ops_actions` is deliberately not
#: here and must never be added: it is the ONE write server, and the control on
#: it is `for_role` plus the approval gate, not a claim that it serves no writes.
#:
#: `telemetry` belongs here for a reason worth stating: a read of production is
#: not a harmless operation, and a PromQL query can be expensive. What makes it
#: a read is that it changes nothing an incident is judged by -- no flag, no
#: replica count, no ticket. Anything on that server that acquires a side
#: effect must leave this set in the same commit.
READ_ONLY_SERVERS = frozenset({"runbooks", "telemetry"})


def _in_process_registry():
    """Every server in SERVERS, running in this process.

    No port, no subprocess, `semantic=False` so no torch -- so these guards run
    in CI against the real servers and the real annotations rather than against
    a description of them.
    """
    from mcp.client import Client
    from oncall_tools.registry import load_tools
    from oncall_tools.servers.ops_actions import build_server as ops_server
    from oncall_tools.servers.runbooks_kb import build_server as runbooks_server
    from oncall_tools.servers.telemetry import build_server as telemetry_server

    def connect(server):
        if server.name == "runbooks":
            return Client(runbooks_server(semantic=False))
        if server.name == "ops_actions":
            return Client(ops_server())
        if server.name == "telemetry":
            # `.invalid` is reserved and cannot resolve: the endpoints are
            # built, nothing is reached.
            return Client(telemetry_server(host="telemetry.invalid"))
        raise AssertionError(f"no in-process client for {server.name!r}: add one here")

    return load_tools(connect=connect)


def test_read_only_servers_still_declare_their_tools_read_only() -> None:
    """Write marking comes off the wire and fails closed, so the whole scheme
    rests on each server declaring `read_only_hint` honestly.

    A server that stops declaring a tool read-only, or starts serving a write
    tool it did not before, fails here rather than in a demo.

    NOTE on what is NOT asserted here: checking `assert_read_only` against
    `for_role(registry, "diagnosis")` would be vacuous, because `for_role`
    builds that view by filtering writes out -- it cannot contain one by
    construction. Measured: with a server's annotation flipped to
    `read_only_hint=False`, that assertion still passed.
    """
    with _in_process_registry() as registry:
        assert registry.names, "the registry loaded no tools at all"

        offenders = sorted(
            tool.name for tool in registry if tool.server in READ_ONLY_SERVERS and tool.write
        )
        assert not offenders, (
            f"{offenders} are served by a read-only server but are not declared "
            "read_only_hint=True, so they would be withheld from every diagnosis agent"
        )


def test_the_write_server_declares_its_tools_as_writes() -> None:
    """The other direction, and the one that was untestable until ops_actions
    existed: a write tool that forgot to say so.

    `_is_write` fails closed, so an unannotated tool is already treated as a
    write -- this catches the tool that declares `read_only_hint=True` while
    changing the running system, which fails closed into the WRONG state.
    """
    with _in_process_registry() as registry:
        from oncall_tools.registry import SERVERS

        write_servers = sorted(set(SERVERS) - READ_ONLY_SERVERS)
        assert write_servers == ["ops_actions"], "the set of write servers changed"

        liars = sorted(tool.name for tool in registry if tool.server == "ops_actions" and tool.read)
        assert not liars, (
            f"{liars} are served by the write server but declare themselves read-only, "
            "so they would be handed to every diagnosis agent"
        )


def test_no_diagnosis_registry_holds_a_write_tool() -> None:
    """The graded requirement, now that there is a write tool to withhold.

    Until ops_actions existed this could only be tested against a synthetic
    registry: every real registry's `writes()` was empty, so the assertion was
    true by there being nothing to isolate. It now runs against the real
    servers, and the count check is what proves the filtering did work rather
    than that there was nothing to filter.
    """
    from oncall_tools.permissions import assert_read_only, for_role

    with _in_process_registry() as registry:
        writes = registry.writes().names
        assert writes, "no write tools in the registry -- this guard would prove nothing"

        diagnosis = for_role(registry, "diagnosis")
        assert_read_only(diagnosis)
        assert len(diagnosis) == len(registry) - len(writes)
        assert not set(diagnosis.names) & set(writes)


def test_actions_match_the_tool_schemas() -> None:
    """Every action a runbook names must exist in the MCP tool contracts.

    Until oncall_contracts.tools exists the validator checks against a
    hand-written ACTION_NAMES, so "the args match the contract" is true for
    set_flag only by coincidence of one person writing both.
    """
    tools = pytest.importorskip(
        "oncall_contracts.tools",
        reason="activates when oncall_contracts.tools declares the MCP tool schemas",
    )
    from oncall_knowledge.schema import ACTION_NAMES

    declared = set(getattr(tools, "ACTION_SCHEMAS", {}))
    assert declared, "oncall_contracts.tools must declare ACTION_SCHEMAS"
    unknown = {a for a in ACTION_NAMES if a != "none"} - declared
    assert not unknown, f"actions with no tool schema: {sorted(unknown)}"


def test_the_planner_and_the_write_server_agree_on_every_payload() -> None:
    """The planner's typed arguments must match the MCP tool contract, per action.

    `test_actions_match_the_tool_schemas` above checks that every action NAME a
    runbook may use has a tool schema. It says nothing about the arguments, and
    for three PRs `set_flag` therefore had two incompatible definitions in two
    owners' packages: `{flag, enabled: bool}` in the planner, `{flag, variant}`
    on the server. Both were internally consistent and fully tested. Nothing
    could fail, because `build_graph` refuses a non-empty tool dict -- so the
    planner and the server had never once had to agree.

    Asserted per action rather than in aggregate, and `set_flag` is asserted to
    be in both maps first: a guard that compared two empty intersections would
    pass forever.
    """
    from oncall_contracts.tools import ACTION_SCHEMAS
    from oncall_graph.planning.plan import ACTION_ARGS

    assert "set_flag" in ACTION_ARGS, "the planner no longer declares set_flag"
    assert "set_flag" in ACTION_SCHEMAS, "the contract no longer declares set_flag"

    for action, schema in ACTION_SCHEMAS.items():
        if action not in ACTION_ARGS:
            continue  # an action the planner cannot propose; nothing to disagree about
        planner = set(ACTION_ARGS[action])
        contract = set(schema.get("properties") or {})
        assert planner == contract, (
            f"{action}: the planner takes {sorted(planner)} and the tool declares "
            f"{sorted(contract)} -- one of them will be refused on the wire"
        )


def test_every_package_declares_the_workspace_members_it_imports() -> None:
    """An `oncall_*` import must appear in that package's own dependencies.

    `test_dependency_direction` above checks which packages a package MAY
    import. This checks that what it DOES import is installed when it is built
    alone. The two are independent, and the gap between them shipped: #19 moved
    the flagd writer into `oncall_contracts` and `oncall_lab/main.py` began
    importing it, but `oncall-lab`'s `[project.dependencies]` was never updated.

    Nothing could catch it. `make test` runs in the root venv, where all seven
    members are installed, so every test passed. `uv sync --package oncall-lab`
    installs only what the member declares, so the CONTAINER had no
    `oncall_contracts` -- and `make images` builds the lab without running it,
    because its runtime needs mounts. A clean build of a broken image.

    Checked against the declared name (`oncall-contracts`) rather than the
    module (`oncall_contracts`), since that is what uv resolves.
    """
    pattern = re.compile(r"^\s*(?:from|import)\s+(oncall_\w+)", re.MULTILINE)
    offenders: list[str] = []

    for package in sorted(p.name for p in PACKAGES.iterdir() if p.is_dir()):
        manifest = PACKAGES / package / "pyproject.toml"
        if not manifest.is_file():
            continue
        declared = tomllib.loads(manifest.read_text(encoding="utf-8"))
        own_module = package.replace("-", "_")
        requires = declared.get("project", {}).get("dependencies", [])
        # Extras too: oncall-tools reaches oncall-knowledge through `semantic`.
        for extra in (declared.get("project", {}).get("optional-dependencies") or {}).values():
            requires = [*requires, *extra]
        names = {re.split(r"[\[><=!;\s]", spec, maxsplit=1)[0] for spec in requires}

        source = PACKAGES / package / "src"
        for path in source.rglob("*.py") if source.is_dir() else []:
            if "__pycache__" in path.parts:
                continue
            for module in pattern.findall(path.read_text(encoding="utf-8")):
                if module == own_module:
                    continue
                if module.replace("_", "-") not in names:
                    offenders.append(
                        f"{path.relative_to(ROOT)} imports {module}, "
                        f"but {package} does not declare {module.replace('_', '-')}"
                    )

    assert not offenders, "undeclared workspace imports:\n  " + "\n  ".join(sorted(set(offenders)))


def test_ci_runs_the_guards_and_cannot_be_softened() -> None:
    """CI must run the guards, and no job may be allowed to fail quietly.

    The guards exist because every failure this project has hit looked like
    success. A `continue-on-error` on the guards job would restore exactly the
    situation they were written to prevent — and it is a tempting one-line fix
    when a guard blocks a push.
    """
    import yaml

    workflow = ROOT / ".github" / "workflows" / "ci.yaml"
    assert workflow.is_file(), "no CI workflow: the guards are enforced only when someone remembers"

    config = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    jobs = config["jobs"]

    for required in ("lint", "test", "guards"):
        assert required in jobs, f"CI is missing the {required!r} job"

    for name, job in jobs.items():
        assert "continue-on-error" not in job, (
            f"job {name!r} sets continue-on-error; a guard that cannot fail the "
            "build is a convention, and conventions are what the guards replace"
        )
        for step in job.get("steps", []):
            assert "continue-on-error" not in step, f"a step in job {name!r} sets continue-on-error"

    # eval-fixture replays eval/dev. It must never point at the held-out set:
    # CI runs on every push, so that would contaminate it within days.
    # The name eval-gate is reserved for a later check that grades a live run.
    fixture = jobs.get("eval-fixture")
    assert fixture is not None, "CI is missing the eval-fixture job"
    rendered = yaml.safe_dump(fixture)
    assert "make eval-fixture" in rendered, "the CI job must run make eval-fixture"
    assert "heldout" not in rendered, "the CI fixture replay must never run on held-out cases"
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    recipe = makefile.split("eval-fixture:", 1)[1].split("\n\n", 1)[0]
    assert "eval/dev" in recipe, "make eval-fixture must name eval/dev explicitly"
    assert "heldout" not in recipe, "make eval-fixture must never run on held-out cases"


def test_the_index_never_contains_a_labelled_query() -> None:
    """A document that indexes its own query matches itself.

    Measured on this corpus: including `queries` in the index text takes
    Recall@1 from 0.648 to 0.958 and Recall@5 to 1.000. Nothing raises, no run
    fails, and the retrieval experiment becomes a measurement of nothing.

    test_corpus_validates already checks the BODY, which never held the queries.
    The leak is one layer later, where an indexer assembles metadata -- so this
    guard asserts on the assembled entry. It is the fifth failure named in this
    module's docstring, and the last one to get a guard.
    """
    from oncall_knowledge.indexing.text import build_entries
    from oncall_knowledge.ingestion import ingest_runbooks

    documents = ingest_runbooks()
    queries_by_id = {d.document_id: d.metadata.queries for d in documents}

    for entry in build_entries(documents):
        for query in queries_by_id[entry.document_id]:
            assert query not in entry.text, (
                f"{entry.document_id} indexes its own labelled query: {query!r}"
            )


def test_every_index_entry_comes_from_the_data_tree() -> None:
    """Retrieval must never be able to index the answers.

    eval/ holds the fault manifest and the held-out cases. An ingestion glob
    that reached it would raise nothing and would quietly make every score
    meaningless, so the assertion is on the path of what actually got indexed.
    """
    from oncall_knowledge.indexing.text import build_entries
    from oncall_knowledge.ingestion import ingest_runbooks

    for entry in build_entries(ingest_runbooks()):
        parts = Path(entry.source_path).resolve().parts

        assert "data" in parts, f"{entry.document_id} was indexed from {entry.source_path}"
        assert "eval" not in parts, f"{entry.document_id} was indexed from eval/"


def test_every_mcp_server_has_a_compose_service_and_a_verify_line() -> None:
    """`deploy/compose.oncall.yaml` opens with this rule in a comment:

        Every service added here must add its health check to `make verify` in
        the same commit so `make up --wait` remains trustworthy.

    Nothing enforced it. A server in `SERVERS` with no Compose service is
    unreachable under `MCP_TRANSPORT=http` and the agent cannot say why; a
    service with no `verify` line is one `make -C deploy up` reports as
    successful without ever having asked it anything -- which is how the lab
    shipped broken behind five green checks in #19.

    Checked against the port, not just the name: a service that exists under
    the right name on the wrong port passes a name check and fails in Docker.
    """
    compose = (ROOT / "deploy" / "compose.oncall.yaml").read_text(encoding="utf-8")
    verify = (ROOT / "deploy" / "Makefile").read_text(encoding="utf-8")

    from oncall_tools.registry import SERVERS

    missing: list[str] = []
    for name, spec in sorted(SERVERS.items()):
        service = f"mcp-{name.replace('_', '-')}"
        if not re.search(rf"^  {re.escape(service)}:$", compose, re.MULTILINE):
            missing.append(f"{service} has no service in deploy/compose.oncall.yaml")
        if f'"{spec.port}:{spec.port}"' not in compose:
            missing.append(f"{service} does not publish port {spec.port}")
        if f"localhost:{spec.port}/mcp" not in verify:
            missing.append(f"`make -C deploy verify` never asks {service} on port {spec.port}")

    assert not missing, "a server that nothing checks:\n  " + "\n  ".join(missing)


def test_every_service_we_define_restarts_with_the_docker_daemon() -> None:
    """`deploy/compose.oncall.yaml` states this rule in a comment:

        Every service added here must also declare `restart: unless-stopped`.

    Nothing enforced it, and on 2026-11-07 it cost an outage that read as a
    working stack. The Docker daemon went down at 03:12:36Z and came back at
    04:00:35Z. Every container carrying a restart policy came back with it;
    `oncall-service` and `oncall-lab` declared none, so they stayed dead while
    31 containers ran healthy. `make -C deploy verify` printed Prometheus ready
    and the shop frontend 200 before failing on `incident API`, `docker ps -a`
    still showed `0.0.0.0:8000->8000` for the stopped container, and the logs
    ended on `GET /healthz 200 OK`. A correct health check, a healthy service,
    and no crash to find.

    Only the services this overlay DEFINES are checked. The file also patches
    six of the demo's own services (grafana, flagd, checkout and friends) to
    add environment and volumes; those inherit the demo's policy, and the two
    kinds are told apart by `build:`, which only ours have.

    The value is checked, not just the key. `restart: no` is what the two
    broken services effectively had, and it would satisfy a presence check.
    `always` is rejected for the opposite reason: it restarts a container the
    operator deliberately stopped, which would quietly undo `make -C deploy
    stop`.
    """
    import yaml

    compose = yaml.safe_load((ROOT / "deploy" / "compose.oncall.yaml").read_text(encoding="utf-8"))
    ours = {name: body for name, body in compose["services"].items() if "build" in body}

    assert ours, (
        "no service in deploy/compose.oncall.yaml declares build: this guard has gone vacuous"
    )

    offenders = [
        f"{name} declares restart: {body.get('restart', '(nothing)')}"
        for name, body in sorted(ours.items())
        if body.get("restart") != "unless-stopped"
    ]

    assert not offenders, (
        "a service that will not come back when the Docker daemon does:\n  "
        + "\n  ".join(offenders)
    )


def test_make_up_builds_every_image_we_define() -> None:
    """`make -C deploy up` must rebuild every image this repo defines.

    It used to build two of them. `up` read `$(COMPOSE) build oncall-service
    oncall-lab`, written before the three `mcp-*` servers existed and never
    extended, so those containers started from whatever image was last built by
    hand. Measured on 2026-11-07: after `make -C deploy up` on a clean checkout,
    `traces` answered over port 8101 with the PREVIOUS release's shape while the
    source on disk had the new one, and `verify` printed `mcp-telemetry 400` --
    which is that file's own signal for a healthy streamable-http server. A
    stale server that answers is worse than one that is down.

    The Makefile now derives the list with awk from the same file this reads
    with a YAML parser. Two readers of one file is the point: this fails if a
    new service is added and awk does not see it, if the awk is broken by a
    reformat, and if someone shortens the list by hand.

    A plain `docker compose build` is not the alternative -- upstream's compose
    declares a `build:` for all 30 demo services, so it would build the whole
    Astronomy Shop from source. Only the services OUR overlay defines are ours
    to build, and `build:` is what tells them from the six demo services this
    file merely patches.
    """
    import os
    import subprocess

    import yaml

    compose = yaml.safe_load((ROOT / "deploy" / "compose.oncall.yaml").read_text(encoding="utf-8"))
    declared = {name for name, body in compose["services"].items() if "build" in body}
    assert declared, (
        "no service in deploy/compose.oncall.yaml declares build: this guard has gone vacuous"
    )

    # `--no-print-directory`, and MAKELEVEL out of the environment, because this
    # runs UNDER `make guards`: a nested make inherits MAKELEVEL and announces
    # `make[1]: Entering directory ...` on STDOUT, which lands in the middle of
    # the list. It did exactly that on this guard's first CI run while passing
    # on macOS, where make 3.81 stayed quiet -- so the two make versions had to
    # be made to agree before the guard could say anything about the Makefile.
    quiet = {k: v for k, v in os.environ.items() if k not in {"MAKELEVEL", "MAKEFLAGS", "MFLAGS"}}
    printed = subprocess.run(
        ["make", "--no-print-directory", "-C", str(ROOT / "deploy"), "print-our-images"],
        capture_output=True,
        text=True,
        check=True,
        env=quiet,
    ).stdout.split()

    assert set(printed) == declared, (
        "deploy/Makefile and deploy/compose.oncall.yaml disagree about which images are ours.\n"
        f"  the Makefile will build: {sorted(printed)}\n"
        f"  the overlay defines:     {sorted(declared)}\n"
        "A service the Makefile does not build runs from a stale image and says nothing."
    )


def test_the_postmortem_corpus_validates() -> None:
    """The seeds are data a reviewer reads, so a typo must fail here.

    `data/postmortems/*.md` is the committed source of the playbook. A file that
    does not parse contributes no lessons and raises nothing at run time: the
    playbook simply comes up smaller, and a memory-on run quietly becomes a
    memory-off one. The same reasoning as `test_corpus_validates` above.
    """
    from oncall_memory import load_postmortems

    postmortems = load_postmortems()

    assert postmortems, "data/postmortems/ is empty: demo scene 3 has nothing to reject"
    statuses = {lesson.status for p in postmortems for lesson in p.lessons}
    assert "approved" in statuses, "the playbook has no applicable lesson"
    assert "rejected" in statuses, (
        "the project guide requires one rejected lesson and its reason as a deliverable"
    )


def test_postmortems_are_not_in_the_retrieval_index() -> None:
    """Indexing them is a corpus-size change, and it must not happen by accident.

    `oncall_knowledge.experiment` refuses to compare two retrieval runs whose
    corpus differs, because Recall@5 over 21 documents is a different task from
    Recall@5 over 18. So the postmortems join the index all at once, strictly
    before the first scored run or strictly after the last -- never one at a time
    as they are authored. Until then the index is runbooks only, and this is what
    keeps it that way.
    """
    from oncall_knowledge.indexing.text import build_entries
    from oncall_knowledge.ingestion import ingest_runbooks
    from oncall_memory import DEFAULT_POSTMORTEM_DIR

    for entry in build_entries(ingest_runbooks()):
        parts = Path(entry.source_path).resolve().parts
        assert "postmortems" not in parts, f"{entry.document_id} was indexed from a postmortem"

    with pytest.raises(ValueError, match="restricted"):
        ingest_runbooks(runbook_dir=DEFAULT_POSTMORTEM_DIR)
