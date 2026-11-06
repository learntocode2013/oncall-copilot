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

from ..state import IncidentChannel, transcript_lines

MONITOR_ROUTES = ("retry", "replan", "stop", "ready")


def _result(state) -> dict:
    last = state.get("last") or {}
    result = last.get("result")
    return result if isinstance(result, dict) else {}


def top_hypothesis(state):
    """The explanation with the highest confidence. That is the one the run stands on."""
    hypotheses = [item for item in (state.get("hypotheses") or []) if isinstance(item, dict)]
    if not hypotheses:
        return None
    return max(hypotheses, key=lambda item: float(item.get("confidence") or 0))


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
    top = top_hypothesis(state)
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


def _previous_variant(state) -> str:
    """The variant in effect before the approved write, as the write reported it.

    Empty when the last step was not the flag write -- a rollback built from a
    read step's result would restore whatever happened to be in that payload.
    """
    last = state.get("last") or {}
    if (last.get("step") or {}).get("tool") != "set_flag":
        return ""
    previous = (last.get("result") or {}).get("previous")
    return previous.strip() if isinstance(previous, str) and previous.strip() else ""


def code_rollback(state) -> dict:
    """The shop call for the inverse. The contract rollback string is not executed."""
    action = state.get("proposed_action") or {}
    if not action:
        return {}
    name = action.get("action")
    args = dict(action.get("args") or {})
    if name == "set_flag":
        # RESTORE WHAT THE WRITE ITSELF REPORTED, do not infer it. `not enabled`
        # assumed a flag has two states; paymentFailure has seven. The forward
        # write returns the variant it replaced, which is the only exact answer
        # available to this package -- the runbook that names the re-arm variant
        # lives in a corpus `oncall_graph` may not import.
        #
        # `rollback_flag`, not `set_flag`: the write server accepts only `off`
        # pairs through the fix tool and refuses to re-arm a fault through it.
        previous = _previous_variant(state)
        flag = args.get("flag")
        if previous and isinstance(flag, str) and flag.strip():
            return {"tool": "rollback_flag", "args": {"flag": flag, "variant": previous}}
        return {}
    if name in ("restart", "scale", "rollback"):
        service = str(args.get("service") or "")
        return {"tool": "rollback", "args": {"service": service}}
    return {}


def monitor(state: IncidentChannel) -> dict:
    """Pick retry, replan, stop, or ready. Append only the new transcript lines."""
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
        "transcript": transcript_lines("monitor", *lines, f"monitor -> {route}"),
    }
    if route == "stop":
        update["status"] = "ESCALATED"
    if from_verification:
        update["pending_rollback"] = pending or {}
    return update


def route_monitor(state: IncidentChannel) -> str:
    """Conditional edge out of ``monitor``. An unknown route stops."""
    route = state.get("route") or "stop"
    if route not in MONITOR_ROUTES:
        return "stop"
    return route


def apply_retry(state: IncidentChannel) -> dict:
    """Put the timed-out call back at the front of the queue. The plan is unchanged."""
    plan = copy.deepcopy(state.get("plan") or {})
    step = (state.get("last") or {}).get("step")
    if isinstance(step, dict) and step.get("tool"):
        plan["steps"] = [dict(step)] + list(plan.get("steps") or [])
    return {
        "plan": plan,
        "stage": plan.get("stage") or state.get("stage") or "test",
        "status": "RETRY",
        "transcript": transcript_lines("retry", "retry"),
    }


def apply_replan(state: IncidentChannel) -> dict:
    """Drop the failed action and show the plan back at hypotheses.

    The denial stays on ``diagnosis_reports``. The shared hypothesis is not marked.
    Counts as one revision.
    """
    plan = copy.deepcopy(state.get("plan") or {})
    plan["stage"] = "hypotheses"
    plan["steps"] = []
    return {
        "plan": plan,
        "stage": "hypotheses",
        "proposed_action": None,
        "revisions": int(state.get("revisions") or 0) + 1,
        "retries": 0,
        "status": "REPLAN",
        "transcript": transcript_lines("replan", "replan"),
    }


def apply_stop(state: IncidentChannel) -> dict:
    """Revision cap or tool budget. Escalate; do not spend another call."""
    return {"status": "ESCALATED", "transcript": transcript_lines("stop", "stop")}


def apply_ready(state: IncidentChannel) -> dict:
    """The monitor found nothing to correct."""
    return {"status": "READY", "transcript": transcript_lines("ready", "ready")}


def build_monitor_graph():
    """Monitor plus the four edges. Later nodes replace the stand-in targets."""
    graph = StateGraph(IncidentChannel)
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
