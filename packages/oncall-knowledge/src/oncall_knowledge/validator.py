"""Validate the On-Call Copilot Markdown runbook corpus.

Usage from the repository root:

    uv run python -m oncall_knowledge.validator
    uv run python -m oncall_knowledge.validator data/runbooks deploy/flagd/demo.flagd.json

The validator checks metadata and action contracts. It CANNOT prove that a
runbook's operational claims are factually correct; that requires injecting the
fault and observing it. Most of the corpus is `status: structural` — behaviour
never observed — and every one of those passes validation. This module buys
consistency, not trustworthiness.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from .schema import (
    ACTION_NAMES,
    EVIDENCE_STATUSES,
    NO_ACTION_NAME,
    RUNBOOK_KINDS,
    SEVERITIES,
    ActionSpec,
    RunbookDocument,
    ValidationIssue,
    metadata_from_mapping,
)

# packages/oncall-knowledge/src/oncall_knowledge/validator.py -> repo root is
# four levels up. parents[1] lands in src/ and the defaults silently point at a
# directory that does not exist.
REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_RUNBOOK_DIR = REPO_ROOT / "data" / "runbooks"
DEFAULT_FLAGD_FILE = REPO_ROOT / "deploy" / "flagd" / "demo.flagd.json"
# Optional: coverage is skipped when eval/ has not been built yet.
DEFAULT_MANIFEST_FILE = REPO_ROOT / "eval" / "faults" / "manifest.yaml"

FRONT_MATTER_PATTERN = re.compile(
    r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)",
    re.DOTALL,
)


def _normalise_text(value: str) -> str:
    """Normalise whitespace and case for duplicate-query comparisons."""
    return " ".join(value.casefold().split())


def split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """Return (front_matter_mapping, Markdown_body).

    The document must begin with a YAML block delimited by '---'.
    """
    match = FRONT_MATTER_PATTERN.match(text)
    if not match:
        raise ValueError("document must start with YAML front matter delimited by '---'")

    raw_yaml = match.group(1)
    body = text[match.end() :].strip()

    try:
        loaded = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML front matter: {exc}") from exc

    if not isinstance(loaded, dict):
        # ValueError, not TypeError: every caller catches ValueError to turn a
        # bad document into a reported issue rather than a crash.
        raise ValueError("front matter must parse to a YAML mapping")  # noqa: TRY004

    return loaded, body


def load_flag_variants(flagd_path: Path) -> dict[str, set[str]]:
    """Read flag names and variant names from flagd JSON.

    Supports the flagd shape used by the demo: a top-level 'flags' mapping,
    with each flag containing a 'variants' mapping. The function intentionally
    reads the actual JSON so variant sets are not duplicated in Python.
    """
    try:
        config = json.loads(flagd_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read flagd config {flagd_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {flagd_path}: {exc}") from exc

    flags = config.get("flags") if isinstance(config, dict) else None
    if not isinstance(flags, dict):
        # ValueError deliberately; see split_front_matter.
        raise ValueError(  # noqa: TRY004
            f"{flagd_path} must contain a top-level 'flags' object"
        )

    result: dict[str, set[str]] = {}

    for flag_name, definition in flags.items():
        if not isinstance(flag_name, str) or not isinstance(definition, dict):
            continue

        variants = definition.get("variants")
        if isinstance(variants, dict):
            result[flag_name] = {name for name in variants if isinstance(name, str)}

    if not result:
        raise ValueError(
            f"no flag variant mappings found in {flagd_path}; check the flagd JSON structure"
        )

    return result


def _validate_action(
    action: ActionSpec,
    field_name: str,
    path: Path,
    flag_variants: Mapping[str, set[str]],
) -> list[ValidationIssue]:
    """Validate action name, argument shape and flagd variant."""
    issues: list[ValidationIssue] = []

    if action.action not in ACTION_NAMES:
        issues.append(
            ValidationIssue(
                str(path),
                f"unsupported action {action.action!r}",
                field=field_name,
            )
        )
        return issues

    if action.action == NO_ACTION_NAME:
        # Shape is enforced by ActionSpec.from_mapping; nothing to check here.
        return issues

    if action.action == "set_flag":
        expected_keys = {"flag", "variant"}
        actual_keys = set(action.args.keys())

        if actual_keys != expected_keys:
            issues.append(
                ValidationIssue(
                    str(path),
                    "set_flag args must contain exactly 'flag' and 'variant'; "
                    f"found {sorted(actual_keys)!r}",
                    field=field_name,
                )
            )
            return issues

        flag = action.args.get("flag")
        variant = action.args.get("variant")

        # YAML 1.1 parsers may parse unquoted `off` and `on` as Booleans.
        # Reject this instead of silently converting it back to a string.
        if not isinstance(flag, str) or not flag:
            issues.append(
                ValidationIssue(
                    str(path),
                    "'flag' must be a non-empty string",
                    field=f"{field_name}.args.flag",
                )
            )
            return issues

        if not isinstance(variant, str) or not variant:
            issues.append(
                ValidationIssue(
                    str(path),
                    "'variant' must be a non-empty string; quote YAML values "
                    "such as 'off' and 'on' to prevent Boolean parsing",
                    field=f"{field_name}.args.variant",
                )
            )
            return issues

        allowed_variants = flag_variants.get(flag)
        if allowed_variants is None:
            issues.append(
                ValidationIssue(
                    str(path),
                    f"flag {flag!r} does not exist in the demo flagd config",
                    field=f"{field_name}.args.flag",
                )
            )
        elif variant not in allowed_variants:
            issues.append(
                ValidationIssue(
                    str(path),
                    f"variant {variant!r} is not valid for flag {flag!r}; "
                    f"allowed variants: {sorted(allowed_variants)!r}",
                    field=f"{field_name}.args.variant",
                )
            )

    return issues


def validate_document(
    path: Path,
    flag_variants: Mapping[str, set[str]],
) -> tuple[RunbookDocument | None, list[ValidationIssue]]:
    """Parse and validate one Markdown runbook."""
    issues: list[ValidationIssue] = []
    relative_path = str(path)

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, [ValidationIssue(relative_path, f"cannot read file: {exc}")]

    try:
        raw, body = split_front_matter(text)
        metadata = metadata_from_mapping(raw)
    except ValueError as exc:
        return None, [ValidationIssue(relative_path, str(exc))]
    except Exception as exc:  # noqa: BLE001
        # Deliberately blind: one malformed document must not stop the other 17
        # from being validated. The document is reported, not swallowed.
        return None, [
            ValidationIssue(
                relative_path,
                f"unexpected parsing error: {type(exc).__name__}: {exc}",
            )
        ]

    if metadata.kind not in RUNBOOK_KINDS:
        issues.append(
            ValidationIssue(
                relative_path,
                f"kind must be one of {sorted(RUNBOOK_KINDS)!r}",
                field="kind",
            )
        )

    if metadata.status not in EVIDENCE_STATUSES:
        issues.append(
            ValidationIssue(
                relative_path,
                f"status must be one of {sorted(EVIDENCE_STATUSES)!r}",
                field="status",
            )
        )

    if metadata.severity not in SEVERITIES:
        issues.append(
            ValidationIssue(
                relative_path,
                f"severity must be one of {sorted(SEVERITIES)!r}",
                field="severity",
            )
        )

    if not metadata.queries:
        issues.append(
            ValidationIssue(
                relative_path,
                "at least one labelled retrieval query is required",
                field="queries",
            )
        )

    # Cause docs represent a particular fault. Symptom docs narrow down causes
    # and do not need to name a fault_id.
    if metadata.kind == "cause" and not metadata.fault_id:
        issues.append(
            ValidationIssue(
                relative_path,
                "cause runbooks must define fault_id",
                field="fault_id",
            )
        )

    # A symptom runbook should narrow the investigation, not directly prescribe
    # a remediation. This protects against the healthy-system flood trap.
    if metadata.kind == "symptom":
        if metadata.fix is not None:
            issues.append(
                ValidationIssue(
                    relative_path,
                    "symptom runbooks must not declare a fix; "
                    "they narrow the diagnosis to cause runbooks",
                    field="fix",
                )
            )
        if metadata.rollback is not None:
            issues.append(
                ValidationIssue(
                    relative_path,
                    "symptom runbooks must not declare a rollback",
                    field="rollback",
                )
            )

    if metadata.kind == "cause":
        if metadata.fix is None:
            issues.append(
                ValidationIssue(
                    relative_path,
                    "cause runbooks must declare a structured fix; a documented "
                    f"no-op is 'action: {NO_ACTION_NAME}' with a reason",
                    field="fix",
                )
            )
        elif metadata.fix.is_executable:
            # Anything that changes the world must be reversible.
            if metadata.rollback is None:
                issues.append(
                    ValidationIssue(
                        relative_path,
                        "executable fixes must define a structured rollback",
                        field="rollback",
                    )
                )
            issues.extend(_validate_action(metadata.fix, "fix", path, flag_variants))
        else:
            issues.extend(_validate_action(metadata.fix, "fix", path, flag_variants))

        if metadata.rollback is not None:
            issues.extend(_validate_action(metadata.rollback, "rollback", path, flag_variants))

    # Symptom runbooks must name at least one candidate cause, or they narrow
    # nothing. The candidates themselves are resolved corpus-wide below.
    if metadata.kind == "symptom" and not metadata.candidates:
        issues.append(
            ValidationIssue(
                relative_path,
                "symptom runbooks must name at least one candidate cause",
                field="candidates",
            )
        )

    # Queries are authored in front matter for evaluation, but must not be
    # copied verbatim from the document body. Otherwise retrieval tests can
    # match the document against its own opening sentence.
    normalised_body = _normalise_text(body)
    for index, query in enumerate(metadata.queries):
        normalised_query = _normalise_text(query)
        if normalised_query and normalised_query in normalised_body:
            issues.append(
                ValidationIssue(
                    relative_path,
                    "labelled query appears verbatim in the Markdown body; "
                    "rewrite it as a distinct user-style query",
                    field=f"queries[{index}]",
                )
            )

    document_id = metadata.fault_id or path.stem

    document = RunbookDocument(
        document_id=document_id,
        source_path=relative_path,
        metadata=metadata,
        body=body,
    )
    return document, issues


def validate_corpus(
    runbook_dir: Path = DEFAULT_RUNBOOK_DIR,
    flagd_path: Path = DEFAULT_FLAGD_FILE,
    manifest_path: Path | None = DEFAULT_MANIFEST_FILE,
) -> tuple[list[RunbookDocument], list[ValidationIssue]]:
    """Validate every Markdown runbook and check IDs are unique.

    Returns:
        (parsed_documents, validation_issues)

    The caller decides whether warnings or errors should fail CI. Currently
    every issue emitted by this module is an error.
    """
    try:
        flag_variants = load_flag_variants(flagd_path)
    except ValueError as exc:
        return [], [ValidationIssue(str(flagd_path), str(exc))]

    if not runbook_dir.is_dir():
        return [], [
            ValidationIssue(
                str(runbook_dir),
                "runbook directory does not exist",
            )
        ]

    paths = sorted(runbook_dir.glob("*.md"))
    if not paths:
        return [], [
            ValidationIssue(
                str(runbook_dir),
                "no Markdown runbooks found",
            )
        ]

    documents: list[RunbookDocument] = []
    issues: list[ValidationIssue] = []

    for path in paths:
        document, document_issues = validate_document(path, flag_variants)
        issues.extend(document_issues)
        if document is not None:
            documents.append(document)

    seen_ids: dict[str, str] = {}
    for document in documents:
        previous_path = seen_ids.get(document.document_id)
        if previous_path is not None:
            issues.append(
                ValidationIssue(
                    document.source_path,
                    f"duplicate document ID {document.document_id!r}; also used by {previous_path}",
                    field="fault_id",
                )
            )
        else:
            seen_ids[document.document_id] = document.source_path

    # Every candidate a symptom names must resolve to a cause runbook that
    # exists. A dangling candidate sends the agent looking for a document that
    # is not there, which looks like a retrieval failure rather than a typo.
    cause_ids = {
        d.metadata.fault_id for d in documents if d.metadata.kind == "cause" and d.metadata.fault_id
    }
    for document in documents:
        for candidate in document.metadata.candidates:
            if candidate not in cause_ids:
                issues.append(
                    ValidationIssue(
                        document.source_path,
                        f"candidate {candidate!r} does not resolve to any cause runbook's fault_id",
                        field="candidates",
                    )
                )

    issues.extend(check_flag_baseline(flagd_path))
    issues.extend(check_fault_coverage(documents, manifest_path))
    return documents, issues


def check_flag_baseline(flagd_path: Path) -> list[ValidationIssue]:
    """Every flag in the committed baseline must be off.

    deploy/flagd/demo.flagd.json is version-controlled and mounted into flagd,
    so a fault left enabled here ships to every teammate who pulls — their shop
    is broken before they run anything. This has already happened once: an
    experiment left paymentFailure at "100%" in a tracked file.

    Faults are injected at RUN time by the injector, never committed.

    TODO: move to tests/test_guards.py once that exists; it lives here only
    because the validator is currently the one thing that runs.
    """
    try:
        config = json.loads(flagd_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [ValidationIssue(str(flagd_path), f"cannot read flagd config: {exc}")]

    issues: list[ValidationIssue] = []
    for name, definition in (config.get("flags") or {}).items():
        if not isinstance(definition, dict):
            continue
        variant = definition.get("defaultVariant")
        if variant != "off":
            issues.append(
                ValidationIssue(
                    str(flagd_path),
                    f"flag {name!r} is committed as {variant!r}, not 'off'; "
                    "a fault left enabled in the baseline breaks the shop for "
                    "everyone who pulls",
                    field=f"flags.{name}.defaultVariant",
                )
            )
    return issues


def check_fault_coverage(
    documents: Sequence[RunbookDocument],
    manifest_path: Path | None,
) -> list[ValidationIssue]:
    """Check the corpus and the fault manifest agree, in both directions.

    Catches "we added a fault type and forgot the runbook" and "we wrote a
    runbook for a fault we never inject". Skipped when no manifest is supplied
    or it does not exist yet, so corpus validation never depends on eval/
    being built.

    Reads the manifest as DATA. It does not import oncall_eval, which nothing
    may import.
    """
    if manifest_path is None or not manifest_path.is_file():
        return []

    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return [ValidationIssue(str(manifest_path), f"cannot read manifest: {exc}")]

    faults = manifest.get("faults")
    if not isinstance(faults, list):
        return [ValidationIssue(str(manifest_path), "manifest must contain a 'faults' list")]

    manifest_ids = {f.get("fault_id") for f in faults if isinstance(f, Mapping)} - {None}
    corpus_ids = {
        d.metadata.fault_id for d in documents if d.metadata.kind == "cause" and d.metadata.fault_id
    }

    issues: list[ValidationIssue] = []
    for missing in sorted(manifest_ids - corpus_ids):
        issues.append(
            ValidationIssue(
                str(manifest_path),
                f"fault {missing!r} is injected but has no runbook",
                field="faults",
            )
        )
    for missing in sorted(corpus_ids - manifest_ids):
        issues.append(
            ValidationIssue(
                str(manifest_path),
                f"runbook {missing!r} describes a fault the manifest never injects",
                field="faults",
                severity="warning",
            )
        )
    return issues


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; suitable for local checks and CI."""
    args = list(argv if argv is not None else sys.argv[1:])

    runbook_dir = Path(args[0]) if len(args) >= 1 else DEFAULT_RUNBOOK_DIR
    flagd_path = Path(args[1]) if len(args) >= 2 else DEFAULT_FLAGD_FILE
    manifest_path = Path(args[2]) if len(args) >= 3 else DEFAULT_MANIFEST_FILE

    documents, issues = validate_corpus(runbook_dir, flagd_path, manifest_path)

    for issue in issues:
        print(issue, file=sys.stderr)

    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity != "error"]
    print(
        f"Validated {len(documents)} runbooks: {len(errors)} error(s), {len(warnings)} warning(s)."
    )

    # Only errors fail the build. A runbook for a fault not yet in the manifest
    # is expected while the corpus leads the eval suite.
    if errors:
        return 1

    print("Corpus validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
