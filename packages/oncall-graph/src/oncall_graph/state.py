"""LangGraph channel for one incident.

``oncall_contracts.state.IncidentState`` is the shared incident. This channel
carries those nine fields in that shape, plus the bookkeeping only the graph
reads. The two types stay different so a node cannot be mistaken for the
contract.
"""

from __future__ import annotations

import operator
from typing import Annotated, NotRequired, TypedDict

from oncall_contracts.config import Severity
from oncall_contracts.state import (
    Alert,
    EvidenceRef,
    Hypothesis,
    Plan,
    ProposedAction,
    Scope,
    TranscriptEntry,
    Verification,
)

from .config import DEFAULT_MAX_REVISIONS, DEFAULT_TOOL_BUDGET
from .planning.plan import validate_action, validate_hypotheses, validate_plan

__all__ = ["IncidentChannel", "new_incident", "transcript_lines"]


class DiagnosisReport(TypedDict):
    """One specialist's verdict. Statements go to ``hypotheses``, not here."""

    service: str
    local_cause: bool
    tool_calls: int


class IncidentChannel(TypedDict):
    """Shared incident fields, then the private channel."""

    alert: Alert
    scope: NotRequired[Scope]
    severity: NotRequired[Severity]
    hypotheses: Annotated[list[Hypothesis], operator.add]
    evidence: Annotated[list[EvidenceRef], operator.add]
    plan: NotRequired[Plan]
    verification: NotRequired[Verification]
    transcript: Annotated[list[TranscriptEntry], operator.add]
    proposed_action: NotRequired[ProposedAction | None]

    # Private. No other package needs these.
    incident_id: str
    stage: str
    approval_payload_hash: str
    last: dict
    ok: bool
    why: str
    route: str
    retries: int
    revisions: int
    max_revisions: int
    tool_budget: int
    diagnosis_reports: Annotated[list[DiagnosisReport], operator.add]
    # What triage asks diagnosis to test. Not ``hypotheses``: those append, and
    # diagnosis appends the scored version of each candidate.
    triage_candidates: list[dict]
    # How many diagnosis tool calls are already included in ``tool_calls``.
    # Parallel copies cannot each write ``tool_calls``; the tally adds the delta.
    diagnosis_calls_accounted: int
    # Set after a failed verification so the next monitor pass does not roll
    # back again, and so the commander does not repeat the same runbook.
    reinvestigate: bool
    pending_rollback: dict

    # The service reads these. They are not on the shared incident.
    status: str
    meter_rows: Annotated[list[dict], operator.add]
    tool_calls: int
    # Node names in visit order. Each node appends its own name, so a loop
    # shows the same name again instead of wiping the earlier visits.
    graph_path: Annotated[list[str], operator.add]


def transcript_lines(kind: str, *texts: str) -> list[TranscriptEntry]:
    """Timeline lines for one node. The channel appends them."""
    return [{"kind": kind, "text": text} for text in texts]


def coerce_alert(alert) -> Alert:
    """Store a contract alert. A plain string is the summary of a firing alert."""
    if isinstance(alert, str):
        return {
            "status": "firing",
            "labels": {"alertname": "alert", "service": "unknown"},
            "annotations": {"summary": alert},
            "startsAt": "",
        }
    if not isinstance(alert, dict):
        raise TypeError("alert must be an object")
    return alert


def new_incident(
    alert="",
    incident_id="incident",
    plan=None,
    hypotheses=None,
    proposed_action=None,
    tool_budget=DEFAULT_TOOL_BUDGET,
    max_revisions=DEFAULT_MAX_REVISIONS,
    **overrides,
) -> IncidentChannel:
    """A running incident. The plan, hypotheses, and action are validated first."""
    cleaned = validate_plan({"stage": "scope"} if plan is None else plan)
    action = validate_action(proposed_action) if proposed_action else None
    state = {
        "alert": coerce_alert(alert),
        "hypotheses": validate_hypotheses(hypotheses),
        "evidence": [],
        "plan": cleaned,
        "transcript": [],
        "proposed_action": action,
        "incident_id": incident_id,
        "stage": cleaned["stage"],
        "approval_payload_hash": "",
        "last": {},
        "ok": True,
        "why": "",
        "route": "",
        "retries": 0,
        "revisions": 0,
        "max_revisions": max_revisions,
        "tool_budget": tool_budget,
        "diagnosis_reports": [],
        "triage_candidates": [],
        "diagnosis_calls_accounted": 0,
        "reinvestigate": False,
        "pending_rollback": {},
        "status": "RUNNING",
        "meter_rows": [],
        "tool_calls": 0,
        "graph_path": [],
    }
    state.update(overrides)
    if "stage" in overrides and isinstance(state.get("plan"), dict):
        state["plan"] = {**state["plan"], "stage": state["stage"]}
    return state
