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
    "oncall_tools": {"oncall_contracts"},
    "oncall_graph": {"oncall_contracts", "oncall_knowledge", "oncall_tools"},
    "oncall_service": {"oncall_contracts", "oncall_graph"},
    "oncall_eval": {"oncall_contracts"},
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


def test_ingestion_globs_exclude_the_eval_tree() -> None:
    """Retrieval must never be able to index the answers."""
    ingest = pytest.importorskip(
        "oncall_knowledge.ingest",
        reason="activates when oncall_knowledge.ingest exposes ingestion_globs()",
    )
    for glob in ingest.ingestion_globs():
        assert glob.startswith("data/"), (
            f"ingestion glob {glob!r} must be rooted in data/; eval/ holds the answers"
        )


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

    # eval-gate does not exist yet. When it does, it must never point at the
    # held-out set: CI runs on every push, so that would contaminate it within
    # days and the headline number would measure nothing.
    gate = jobs.get("eval-gate")
    if gate is not None:
        rendered = yaml.safe_dump(gate)
        assert "heldout" not in rendered, "the CI eval gate must never run on held-out cases"
        assert "eval/dev" in rendered, "the CI eval gate must name eval/dev explicitly"
