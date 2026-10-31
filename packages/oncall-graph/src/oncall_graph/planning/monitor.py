"""L10's monitor, for an incident instead of a trip.

``monitor`` is code. It reads the last result and the plan, then picks one edge:

* ``retry`` — a tool timed out, and this call has not been retried yet
* ``replan`` — the retry also timed out, the evidence contradicts the top
  hypothesis, a tool failed, or verification failed (after a code rollback)
* ``stop`` — the tool budget or the revision cap is spent; the run escalates
* ``ready`` — nothing above fired, so the graph may leave the monitor

A failed verification rolls back in code here (the inverse of the one proposed
action) and then takes the replan edge. A spent tool budget or revision cap
turns that replan into stop after the rollback is recorded. The model does
not choose the edge.
"""

from __future__ import annotations

import copy

from langgraph.graph import END, START, StateGraph

from ..state import IncidentState
from .plan import ACTION_TOOLS

MONITOR_ROUTES = ("retry", "replan", "stop", "ready")


def _result(state) -> dict:
    last = state.get("last") or {}
    result = last.get("result")
    return result if isinstance(result, dict) else {}


def top_hypothesis(plan):
    """The hypothesis the commander is currently standing on. Rank 1 is first."""
    hypotheses = [item for item in (plan or {}).get("hypotheses") or [] if isinstance(item, dict)]
    if not hypotheses:
        return None
    return min(hypotheses, key=lambda item: item.get("rank", 10**9))


def _denies_root_cause(report) -> bool:
    if report.get("local_cause") is False:
        return True
    return report.get("supports_root_cause") is False


def evidence_contradicts(state) -> bool:
    """True when the evidence says the top hypothesis is the wrong cause.

    This is the signature-A case: the commander blamed one service, and that
    service's diagnosis report says the fault is not local.
    """
    result = _result(state)
    if result.get("contradicts_hypothesis") or result.get("contradicts"):
        return True
    top = top_hypothesis(state.get("plan") or {})
    if top and top.get("contradicted"):
        return True
    if not top:
        return False
    service = (top.get("service") or "").strip().lower()
    if not service:
        return False
    reports = state.get("diagnosis_reports") or []
    own = [
        report
        for report in reports
        if isinstance(report, dict) and (report.get("service") or "").strip().lower() == service
    ]
    return any(_denies_root_cause(report) for report in own)


def _timeout(state) -> bool:
    result = _result(state)
    if result.get("timeout") is True:
        return True
    text = str(result.get("error") or "")
    if state.get("ok") is False or result.get("error"):
        lowered = text.lower()
        if "timeout" in lowered or "timed out" in lowered:
            return True
    return False


def _tool_failed(state) -> bool:
    if _timeout(state):
        return False
    if state.get("ok") is False:
        return True
    return bool(_result(state).get("error"))


def _budget_left(state) -> bool:
    budget = state.get("tool_budget")
    if budget is None:
        return True
    return int(state.get("tool_calls") or 0) < int(budget)


def _verification_failed(state) -> bool:
    verification = state.get("verification") or {}
    return verification.get("recovered") is False


def code_rollback(state) -> dict:
    """The allow-listed inverse of the proposed action. This does not touch the shop."""
    action = (state.get("plan") or {}).get("proposed_action") or {}
    if not action:
        return {}
    rollback = action.get("rollback")
    if isinstance(rollback, dict) and rollback.get("tool") in ACTION_TOOLS:
        return {"tool": rollback["tool"], "args": dict(rollback.get("args") or {})}
    if isinstance(rollback, str) and rollback.strip():
        return {"procedure": rollback.strip()}
    tool = action.get("tool")
    args = dict(action.get("args") or {})
    if tool == "set_flag" and isinstance(args.get("enabled"), bool):
        return {"tool": "set_flag", "args": {**args, "enabled": not args["enabled"]}}
    if tool in ACTION_TOOLS:
        service = str(args.get("service") or "")
        return {"tool": "rollback", "args": {"service": service}}
    return {}


def monitor(state: IncidentState) -> dict:
    """Pick retry, replan, stop, or ready. Append only the new trace lines."""
    retries = int(state.get("retries") or 0)
    revisions = int(state.get("revisions") or 0)
    cap = state.get("max_revisions")
    max_revisions = 3 if cap is None else int(cap)
    result = _result(state)

    route, why = "ready", ""
    pending = None
    from_verification = False
    lines = []

    if result.get("budget_exhausted") is True:
        route, why = "stop", "tool budget exhausted"
    elif _timeout(state) and retries == 0 and _budget_left(state):
        route, why = "retry", "tool timeout"
    elif _timeout(state):
        why = "tool timeout after retry" if retries else "tool timeout"
        route = "replan"
    elif _tool_failed(state):
        route, why = "replan", str(result.get("error") or "tool failed")
    elif evidence_contradicts(state):
        route, why = "replan", "evidence contradicts the top hypothesis"
    elif _verification_failed(state):
        pending = code_rollback(state)
        if pending:
            lines.append("rollback")
        route, why = "replan", "verification failed; rolled back in code"
        from_verification = True

    if route == "replan" and revisions >= max_revisions:
        route, why = "stop", "revision cap: " + why
    elif route == "replan" and not _budget_left(state):
        route, why = "stop", "tool budget exhausted: " + why
    elif route == "retry" and not _budget_left(state):
        route, why = "stop", "tool budget exhausted"

    update = {
        "route": route,
        "why": why,
        "retries": retries + 1 if route == "retry" else (0 if route == "ready" else retries),
        "trace": lines + [f"monitor -> {route}"],
    }
    if route == "stop":
        update["status"] = "ESCALATED"
    if from_verification:
        update["pending_rollback"] = pending or {}
    return update


def route_monitor(state: IncidentState) -> str:
    """Conditional edge out of ``monitor``. An unknown route stops."""
    route = state.get("route") or "stop"
    if route not in MONITOR_ROUTES:
        return "stop"
    return route


def _mark_top_contradicted(plan):
    hypotheses = [dict(item) for item in plan.get("hypotheses") or []]
    if not hypotheses:
        plan["hypotheses"] = []
        return
    index = min(range(len(hypotheses)), key=lambda i: hypotheses[i].get("rank", 10**9))
    hypotheses[index]["contradicted"] = True
    plan["hypotheses"] = hypotheses


def apply_retry(state: IncidentState) -> dict:
    """Put the timed-out call back at the front of the queue. The plan is unchanged."""
    plan = copy.deepcopy(state.get("plan") or {})
    step = (state.get("last") or {}).get("step")
    if isinstance(step, dict) and step.get("tool"):
        plan["steps"] = [dict(step)] + list(plan.get("steps") or [])
    return {
        "plan": plan,
        "stage": plan.get("stage") or state.get("stage") or "test",
        "status": "RETRY",
        "trace": ["retry"],
    }


def apply_replan(state: IncidentState) -> dict:
    """Drop the failed action and show the plan back at hypotheses.

    A contradicted top hypothesis stays on the plan, marked, so the next
    commander pass can see why this revision happened. Counts as one revision.
    """
    plan = copy.deepcopy(state.get("plan") or {})
    plan["stage"] = "hypotheses"
    plan["steps"] = []
    plan["proposed_action"] = None
    if "contradict" in (state.get("why") or ""):
        _mark_top_contradicted(plan)
    return {
        "plan": plan,
        "stage": "hypotheses",
        "revisions": int(state.get("revisions") or 0) + 1,
        "retries": 0,
        "status": "REPLAN",
        "trace": ["replan"],
    }


def apply_stop(state: IncidentState) -> dict:
    """Revision cap or tool budget. Escalate; do not spend another call."""
    return {"status": "ESCALATED", "trace": ["stop"]}


def apply_ready(state: IncidentState) -> dict:
    """The monitor found nothing to correct."""
    return {"status": "READY", "trace": ["ready"]}


def build_monitor_graph():
    """Monitor plus the four edges. Later nodes replace the stand-in targets."""
    graph = StateGraph(IncidentState)
    graph.add_node("monitor", monitor)
    graph.add_node("retry", apply_retry)
    graph.add_node("replan", apply_replan)
    graph.add_node("stop", apply_stop)
    graph.add_node("ready", apply_ready)
    graph.add_edge(START, "monitor")
    graph.add_conditional_edges(
        "monitor",
        route_monitor,
        {name: name for name in MONITOR_ROUTES},
    )
    for name in MONITOR_ROUTES:
        graph.add_edge(name, END)
    return graph.compile()
