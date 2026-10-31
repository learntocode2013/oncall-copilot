"""LangGraph channel for one incident.

``oncall_contracts.state.IncidentState`` stays the shared contract. There the
alert is a dict, ``plan`` is a list of step names, and a hypothesis carries
``evidence_refs`` and ``confidence``. This TypedDict is the runtime channel
the nodes read and write: a visible plan object, monitor bookkeeping, and
meter rows. Do not replace the contract with it. The service maps between
the two when it writes a run record.

``stage`` moves scope → hypotheses → test → act → verify → communicate.
Hypotheses carry the evidence that supports them. At most one proposed action
is on the plan, with typed args, the effect we expect, and how to roll it back.
``trace`` appends, the same reducer L10 uses on ``Trip.trace``.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from .config import DEFAULT_MAX_REVISIONS, DEFAULT_TOOL_BUDGET
from .planning.plan import PLAN_STAGES, validate_plan

__all__ = ["PLAN_STAGES", "IncidentState", "new_incident"]


class IncidentState(TypedDict):
    incident_id: str  # Identifier for this incident; later the checkpointer thread id.
    alert: str  # Alert text that opened the incident.
    stage: str  # Current stage, kept equal to plan["stage"].
    plan: dict  # Visible plan: stage, hypotheses with evidence, read steps, one proposed action.
    approval_payload_hash: str  # Hash of the exact action payload the engineer approved.
    verification: dict  # Post-action metric check, including whether the shop recovered.
    meter_rows: Annotated[list[dict], operator.add]  # Model-call rows appended during the run.
    trace: Annotated[
        list[str], operator.add
    ]  # Append-only lines: monitor edges, retry, replan, stop.
    last: dict  # Latest tool step and its result; the monitor reads this.
    ok: bool  # False when that latest tool call failed.
    status: str  # RUNNING, RETRY, REPLAN, READY, or ESCALATED.
    why: str  # Monitor's reason for the route it chose.
    route: str  # Next edge: retry, replan, stop, or ready.
    retries: int  # Timeouts already retried for the current call.
    revisions: int  # Replans already applied.
    max_revisions: int  # Revision cap; the next replan at this cap stops.
    tool_calls: int  # Tool calls already spent on this incident.
    tool_budget: int  # Tool-call cap for the incident: 5, 10, or 20.
    diagnosis_reports: Annotated[
        list[dict], operator.add
    ]  # One report per suspect service; copies concatenate.
    pending_rollback: dict  # Allow-listed inverse of the action after a failed verification.


def new_incident(
    alert="",
    incident_id="incident",
    plan=None,
    tool_budget=DEFAULT_TOOL_BUDGET,
    max_revisions=DEFAULT_MAX_REVISIONS,
    **overrides,
) -> IncidentState:
    """A running incident. ``plan`` is validated before it enters state."""
    cleaned = validate_plan({"stage": "scope"} if plan is None else plan)
    state = {
        "incident_id": incident_id,
        "alert": alert,
        "stage": cleaned["stage"],
        "plan": cleaned,
        "approval_payload_hash": "",
        "verification": {},
        "meter_rows": [],
        "trace": [],
        "last": {},
        "ok": True,
        "status": "RUNNING",
        "why": "",
        "route": "",
        "retries": 0,
        "revisions": 0,
        "max_revisions": max_revisions,
        "tool_calls": 0,
        "tool_budget": tool_budget,
        "diagnosis_reports": [],
        "pending_rollback": {},
    }
    state.update(overrides)
    if "stage" in overrides:
        state["plan"] = {**state["plan"], "stage": state["stage"]}
    return state
