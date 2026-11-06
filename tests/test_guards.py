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
    "oncall_service": {"oncall_contracts", "oncall_graph"},
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
READ_ONLY_SERVERS = frozenset({"runbooks"})


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

    def connect(server):
        if server.name == "runbooks":
            return Client(runbooks_server(semantic=False))
        if server.name == "ops_actions":
            return Client(ops_server())
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
