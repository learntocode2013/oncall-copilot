"""Faults the operator may arm, and the symptom published for each one.

The symptom table is the only alert text this service will send. It is not
derived from the manifest's root cause or correct action. A fault with no
entry can still be armed, and it cannot page the agent.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REQUIRED_FIELDS = {
    "fault_id",
    "enable",
    "disable",
    "root_cause_services",
    "correct_action",
    "detect",
}
OPTIONAL_FIELDS = {"complexity", "recovery"}
SIGNAL_OPERATORS = {">", ">=", "<", "<=", "=="}


class CatalogError(ValueError):
    """The manifest or the symptom table cannot be shown to an operator."""


@dataclass(frozen=True)
class Symptom:
    """The alert fields the agent is allowed to see for one fault."""

    alertname: str
    service: str
    severity: str
    summary: str
    fingerprint: str
    grafana_note: str


@dataclass(frozen=True)
class LabFault:
    """One manifest fault, plus a symptom when this lab knows how to page it."""

    fault_id: str
    flag: str
    arm_variant: str
    disarm_variant: str
    root_cause_services: tuple[str, ...]
    correct_action: dict[str, Any] | None
    detect_query: str
    detect_operator: str
    detect_threshold: float
    symptom: Symptom | None


# Keyed by fault id. The values are the checkout symptom Grafana already posts.
# They must not name the flag or the service that actually fails.
SYMPTOMS: dict[str, Symptom] = {
    "payment-failure": Symptom(
        alertname="CheckoutErrorRateHigh",
        service="checkout",
        severity="critical",
        summary="Checkout error rate above 5% for five minutes",
        fingerprint="checkout-error-rate-high",
        grafana_note=(
            "Grafana posts this same checkout symptom after the error rate "
            "stays above 5% for five minutes. Paging now as well can open a "
            "second investigation, because Grafana uses its own fingerprint."
        ),
    )
}


def load_catalog(path: Path, symptoms: Mapping[str, Symptom] | None = None) -> tuple[LabFault, ...]:
    """Read the fault manifest and attach a symptom only when one is published."""
    table = SYMPTOMS if symptoms is None else dict(symptoms)
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CatalogError(f"cannot read manifest {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise CatalogError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(loaded, Mapping) or not isinstance(loaded.get("faults"), list):
        raise CatalogError("manifest must contain a faults list")
    if not loaded["faults"]:
        raise CatalogError("manifest faults must not be empty")

    faults: list[LabFault] = []
    seen: set[str] = set()
    for position, value in enumerate(loaded["faults"]):
        fault = _fault(value, position, table)
        if fault.fault_id in seen:
            raise CatalogError(f"duplicate fault_id {fault.fault_id!r}")
        seen.add(fault.fault_id)
        faults.append(fault)

    unknown = sorted(set(table) - seen)
    if unknown:
        raise CatalogError(f"symptom mapping has no manifest fault: {unknown}")
    return tuple(faults)


def fault_view(fault: LabFault, current_variant: str | None) -> dict[str, Any]:
    """Operator JSON. Ground truth stays here and out of the paged alert."""
    symptom = None
    grafana_note = None
    if fault.symptom is not None:
        symptom = {
            "alertname": fault.symptom.alertname,
            "service": fault.symptom.service,
            "severity": fault.symptom.severity,
            "summary": fault.symptom.summary,
        }
        grafana_note = fault.symptom.grafana_note
    return {
        "fault_id": fault.fault_id,
        "flag": fault.flag,
        "arm_variant": fault.arm_variant,
        "disarm_variant": fault.disarm_variant,
        "current_variant": current_variant,
        "armed": current_variant == fault.arm_variant,
        "root_cause_services": list(fault.root_cause_services),
        "correct_action": fault.correct_action,
        "detect": {
            "query": fault.detect_query,
            "operator": fault.detect_operator,
            "threshold": fault.detect_threshold,
        },
        "can_page": fault.symptom is not None,
        "symptom": symptom,
        "grafana_note": grafana_note,
    }


def _fault(value: Any, position: int, symptoms: Mapping[str, Symptom]) -> LabFault:
    if not isinstance(value, Mapping):
        raise CatalogError(f"faults[{position}] must be a mapping")
    missing = REQUIRED_FIELDS - set(value)
    unknown = set(value) - REQUIRED_FIELDS - OPTIONAL_FIELDS
    if missing:
        raise CatalogError(f"faults[{position}] missing fields {sorted(missing)}")
    if unknown:
        raise CatalogError(f"faults[{position}] has unknown fields {sorted(unknown)}")

    fault_id = _text(value.get("fault_id"), f"faults[{position}].fault_id")
    flag, arm_variant = _set_flag(value.get("enable"), f"{fault_id}.enable")
    disable_flag, disarm_variant = _set_flag(value.get("disable"), f"{fault_id}.disable")
    if flag != disable_flag:
        raise CatalogError(f"{fault_id} enable and disable must target the same flag")
    if arm_variant == disarm_variant:
        raise CatalogError(f"{fault_id} enable and disable variants must differ")

    services = value.get("root_cause_services")
    if not isinstance(services, list) or not services:
        raise CatalogError(f"{fault_id}.root_cause_services must be a non-empty list")
    if any(not isinstance(service, str) or not service.strip() for service in services):
        raise CatalogError(f"{fault_id}.root_cause_services must contain non-empty strings")

    query, operator, threshold = _signal(value.get("detect"), f"{fault_id}.detect")
    return LabFault(
        fault_id=fault_id,
        flag=flag,
        arm_variant=arm_variant,
        disarm_variant=disarm_variant,
        root_cause_services=tuple(service.strip() for service in services),
        correct_action=_action(value.get("correct_action"), f"{fault_id}.correct_action"),
        detect_query=query,
        detect_operator=operator,
        detect_threshold=threshold,
        symptom=symptoms.get(fault_id),
    )


def _set_flag(value: Any, field: str) -> tuple[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"action", "args"}:
        raise CatalogError(f"{field} must contain exactly action and args")
    if value.get("action") != "set_flag":
        raise CatalogError(f"{field} must be a set_flag action")
    args = value.get("args")
    if not isinstance(args, Mapping) or set(args) != {"flag", "variant"}:
        raise CatalogError(f"{field}.args must contain exactly flag and variant")
    return _text(args.get("flag"), f"{field}.args.flag"), _text(
        args.get("variant"), f"{field}.args.variant"
    )


def _action(value: Any, field: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"action", "args"}:
        raise CatalogError(f"{field} must contain exactly action and args")
    action = _text(value.get("action"), f"{field}.action")
    args = value.get("args")
    if not isinstance(args, Mapping):
        raise CatalogError(f"{field}.args must be a mapping")
    return {"action": action, "args": dict(args)}


def _signal(value: Any, field: str) -> tuple[str, str, float]:
    if not isinstance(value, Mapping) or set(value) != {"query", "operator", "threshold"}:
        raise CatalogError(f"{field} must contain exactly query, operator, and threshold")
    operator = value.get("operator")
    if operator not in SIGNAL_OPERATORS:
        raise CatalogError(f"{field}.operator must be one of {sorted(SIGNAL_OPERATORS)}")
    threshold = value.get("threshold")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise CatalogError(f"{field}.threshold must be a number")
    if not math.isfinite(threshold):
        raise CatalogError(f"{field}.threshold must be finite")
    return _text(value.get("query"), f"{field}.query"), operator, float(threshold)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{field} must be a non-empty string")
    return value.strip()
