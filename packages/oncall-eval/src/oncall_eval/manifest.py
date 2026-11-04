"""Load and validate the fault manifest used as evaluation ground truth."""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import yaml
from oncall_contracts.faults import FaultAction, FaultSignal, FaultSpec

FAULT_FIELDS = {
    "fault_id",
    "enable",
    "disable",
    "root_cause_services",
    "complexity",
    "correct_action",
    "detect",
    "recovery",
}

SIGNAL_FIELDS = {"query", "operator", "threshold"}
SIGNAL_OPERATORS = {">", ">=", "<", "<=", "=="}


def _non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _action(value: Any, field: str, *, nullable: bool = False) -> FaultAction | None:
    if value is None and nullable:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a structured action mapping")  # noqa: TRY004
    if set(value) != {"action", "args"}:
        raise ValueError(f"{field} must contain exactly 'action' and 'args'")
    action = _non_empty_string(value.get("action"), f"{field}.action")
    args = value.get("args")
    if not isinstance(args, Mapping):
        raise ValueError(f"{field}.args must be a mapping")  # noqa: TRY004
    return {"action": action, "args": dict(args)}


def _signal(value: Any, field: str) -> FaultSignal:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a signal mapping")  # noqa: TRY004
    if set(value) != SIGNAL_FIELDS:
        raise ValueError(f"{field} must contain exactly {sorted(SIGNAL_FIELDS)}")
    operator = value.get("operator")
    if operator not in SIGNAL_OPERATORS:
        raise ValueError(f"{field}.operator must be one of {sorted(SIGNAL_OPERATORS)}")
    threshold = value.get("threshold")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ValueError(f"{field}.threshold must be a number")  # noqa: TRY004
    if not math.isfinite(threshold):
        raise ValueError(f"{field}.threshold must be finite")
    return {
        "query": _non_empty_string(value.get("query"), f"{field}.query"),
        "operator": operator,
        "threshold": float(threshold),
    }


def _fault(value: Any, position: int) -> FaultSpec:
    if not isinstance(value, Mapping):
        raise ValueError(f"faults[{position}] must be a mapping")  # noqa: TRY004
    missing = FAULT_FIELDS - set(value)
    unknown = set(value) - FAULT_FIELDS
    if missing:
        raise ValueError(f"faults[{position}] missing fields {sorted(missing)}")
    if unknown:
        raise ValueError(f"faults[{position}] has unknown fields {sorted(unknown)}")

    fault_id = _non_empty_string(value.get("fault_id"), f"faults[{position}].fault_id")
    services = value.get("root_cause_services")
    if not isinstance(services, list) or not services:
        raise ValueError(f"{fault_id}.root_cause_services must be a non-empty list")
    if any(not isinstance(service, str) or not service.strip() for service in services):
        raise ValueError(f"{fault_id}.root_cause_services must contain non-empty strings")
    cleaned_services = [service.strip() for service in services]

    expected_complexity = "multi" if len(cleaned_services) > 1 else "single"
    if value.get("complexity") != expected_complexity:
        raise ValueError(
            f"{fault_id}.complexity must be {expected_complexity!r} for "
            f"{len(cleaned_services)} root-cause service(s)"
        )

    return {
        "fault_id": fault_id,
        "enable": cast(FaultAction, _action(value.get("enable"), f"{fault_id}.enable")),
        "disable": cast(FaultAction, _action(value.get("disable"), f"{fault_id}.disable")),
        "root_cause_services": cleaned_services,
        "complexity": expected_complexity,
        "correct_action": _action(
            value.get("correct_action"), f"{fault_id}.correct_action", nullable=True
        ),
        "detect": _signal(value.get("detect"), f"{fault_id}.detect"),
        "recovery": _signal(value.get("recovery"), f"{fault_id}.recovery"),
    }


def load_manifest(path: Path) -> dict[str, FaultSpec]:
    """Return faults keyed by id, rejecting incomplete or ambiguous ground truth."""
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read manifest {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(loaded, Mapping) or set(loaded) != {"faults"}:
        raise ValueError("manifest must contain exactly one top-level 'faults' field")
    values = loaded["faults"]
    if not isinstance(values, list) or not values:
        raise ValueError("manifest faults must be a non-empty list")

    faults: dict[str, FaultSpec] = {}
    for position, value in enumerate(values):
        fault = _fault(value, position)
        fault_id = fault["fault_id"]
        if fault_id in faults:
            raise ValueError(f"duplicate fault_id {fault_id!r}")
        faults[fault_id] = fault
    return faults
