"""Evaluation case schema and manifest cross-checks."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import TypedDict, cast

from oncall_contracts.config import Complexity
from oncall_contracts.faults import FaultAction, FaultSpec
from oncall_contracts.state import Alert


class ExpectedOutcome(TypedDict):
    root_cause_services: list[str]
    correct_action: FaultAction | None
    max_tool_calls: int
    harmful_actions: int


class EvalCase(TypedDict):
    case_id: str
    fault_id: str
    complexity: Complexity
    variant: str
    alert: Alert
    expected: ExpectedOutcome


def load_case(path: Path) -> EvalCase:
    """Load one JSON case. Semantic validation happens against the manifest."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read case {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        # All loader failures use ValueError so the harness can report a bad
        # case without distinguishing JSON syntax from JSON shape.
        raise ValueError(f"case {path} must be a JSON object")  # noqa: TRY004
    return cast(EvalCase, dict(value))


def validate_case(case: EvalCase, faults: Mapping[str, FaultSpec]) -> list[str]:
    """Return every actionable case/manifest mismatch without stopping at the first."""
    problems: list[str] = []
    required = {"case_id", "fault_id", "complexity", "variant", "alert", "expected"}
    if missing := required - set(case):
        return [f"case missing fields {sorted(missing)}"]

    fault_id = case.get("fault_id")
    if not isinstance(fault_id, str) or not fault_id:
        return ["fault_id must be a non-empty string"]
    fault = faults.get(fault_id)
    if fault is None:
        return [f"unknown fault_id {fault_id!r}"]
    if case.get("complexity") != fault["complexity"]:
        problems.append("case complexity disagrees with the manifest")

    expected = case.get("expected")
    if not isinstance(expected, Mapping):
        return problems + ["expected must be an object"]
    if expected.get("root_cause_services") != fault["root_cause_services"]:
        problems.append("expected root_cause_services disagree with the manifest")
    if expected.get("correct_action") != fault["correct_action"]:
        problems.append("expected correct_action disagrees with the manifest")
    budget = expected.get("max_tool_calls")
    if not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:
        problems.append("expected max_tool_calls must be a positive integer")
    if expected.get("harmful_actions") != 0:
        problems.append("expected harmful_actions must be zero")

    alert = case.get("alert")
    if not isinstance(alert, Mapping):
        problems.append("alert must be an object")
    else:
        for field in ("status", "labels", "annotations", "startsAt"):
            if field not in alert:
                problems.append(f"alert missing {field!r}")
    return problems
