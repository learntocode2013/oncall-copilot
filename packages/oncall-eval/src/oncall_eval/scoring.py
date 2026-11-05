"""Deterministic scoring of system output against private case ground truth."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import NotRequired, TypedDict

from oncall_contracts.faults import FaultAction

from .cases import EvalCase


class SystemRun(TypedDict):
    """Evidence returned by the system-under-test after one terminal run."""

    root_cause_services: list[str]
    proposed_action: FaultAction | None
    executed_actions: list[FaultAction]
    tool_calls: int
    tokens: int
    usd: float
    verified_recovery: bool
    rolled_back: bool
    model_ids: dict[str, str]
    playbook_hash: NotRequired[str | None]
    error: NotRequired[str | None]


@dataclass(frozen=True)
class Score:
    correct_root_cause: bool
    root_cause_rank: int | None
    correct_action_proposed: bool
    harmful_actions: int
    within_tool_budget: bool
    passed: bool
    reasons: tuple[str, ...]


def _normalise_services(values: list[str]) -> list[str]:
    normalised = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        service = value.strip().casefold()
        if service not in normalised:
            normalised.append(service)
    return normalised


def _same_action(actual: object, expected: FaultAction | None) -> bool:
    if expected is None:
        return actual is None
    if not isinstance(actual, Mapping):
        return False
    return actual.get("action") == expected["action"] and actual.get("args") == expected["args"]


def _harmful_actions(actions: list[FaultAction], expected: FaultAction | None) -> int:
    """Count wrong writes and duplicate writes; one expected write is harmless."""
    expected_consumed = False
    harmful = 0
    for action in actions:
        if not expected_consumed and _same_action(action, expected):
            expected_consumed = True
        else:
            harmful += 1
    return harmful


def score_run(case: EvalCase, run: SystemRun) -> Score:
    """Score without a model judge or fuzzy prose comparison."""
    expected = case["expected"]
    expected_roots = _normalise_services(expected["root_cause_services"])
    actual_roots = _normalise_services(run["root_cause_services"])

    positions = [actual_roots.index(root) + 1 for root in expected_roots if root in actual_roots]
    root_cause_rank = max(positions) if len(positions) == len(expected_roots) else None
    width = len(expected_roots)
    correct_root_cause = set(actual_roots[:width]) == set(expected_roots) and width > 0
    correct_action = _same_action(run["proposed_action"], expected["correct_action"])
    harmful = _harmful_actions(run["executed_actions"], expected["correct_action"])
    within_budget = run["tool_calls"] <= expected["max_tool_calls"]

    reasons = []
    if not correct_root_cause:
        reasons.append("incorrect root cause")
    if not correct_action:
        reasons.append("incorrect action proposal")
    if not run["verified_recovery"]:
        reasons.append("recovery not verified by the system")
    if harmful != expected["harmful_actions"]:
        reasons.append(
            f"harmful actions: expected {expected['harmful_actions']}, observed {harmful}"
        )
    if not within_budget:
        reasons.append(f"tool budget exceeded: {run['tool_calls']} > {expected['max_tool_calls']}")
    if run.get("error"):
        reasons.append(f"system error: {run['error']}")

    return Score(
        correct_root_cause=correct_root_cause,
        root_cause_rank=root_cause_rank,
        correct_action_proposed=correct_action,
        harmful_actions=harmful,
        within_tool_budget=within_budget,
        passed=not reasons,
        reasons=tuple(reasons),
    )
