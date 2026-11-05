"""Typed hand-offs derived from tool results.

The model chooses which tool to call and writes the explanation. These functions
turn the tool results into the objects the graph stores. A sentence in the
model's reply does not outrank a trace.
"""

from __future__ import annotations

import ast

from langchain_core.messages import AIMessage, ToolMessage

from .briefs import (
    alert_service,
    alert_summary,
    assign_severity,
    budget_left,
    on_purchase_path,
    precondition_matches,
    service_for_flag,
    split_budget,
)
from .config import DEFAULT_MAX_REVISIONS
from .planning.monitor import top_hypothesis
from .planning.plan import format_plan, validate_hypotheses, validate_plan

# Below the commander's opening hypothesis, so a contradicted alert service
# stays on top until the commander corrects it. 0.9 is that opening weight.
DIAGNOSIS_CONFIDENCE = 0.4
COMMANDER_CONFIDENCE = 0.9
CORRECTED_CONFIDENCE = 0.95


def parse_tool_content(content):
    """Tool messages store ``str(result)``. Recover the dict when it is one."""
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        return {"text": str(content)}
    try:
        value = ast.literal_eval(content)
    except (SyntaxError, ValueError):
        return {"text": content}
    if isinstance(value, dict):
        return value
    return {"text": content}


def exchanges_from_messages(messages) -> list[tuple]:
    """``(tool name, args, result)`` in call order. Unparsed text stays a dict."""
    pending = {}
    found = []
    for message in messages or []:
        if isinstance(message, AIMessage):
            for call in message.tool_calls or []:
                pending[call.get("id")] = call
            continue
        if isinstance(message, ToolMessage):
            call = pending.get(message.tool_call_id) or {}
            name = call.get("name") or message.name
            args = call.get("args") or {}
            found.append(
                (
                    str(name or ""),
                    args if isinstance(args, dict) else {},
                    parse_tool_content(message.content),
                )
            )
    return found


def evidence_from_exchanges(exchanges) -> list[dict]:
    """References only. The log line stays inside the tool result for this turn."""
    items = []
    for name, args, result in exchanges:
        if name == "promql":
            ref = str(args.get("query") or result.get("query") or "promql")
            items.append({"kind": "promql", "ref": ref, "source": "shop"})
        elif name == "log_search":
            items.append(
                {"kind": "log_query", "ref": str(args.get("service") or "logs"), "source": "shop"}
            )
        elif name == "traces":
            ref = str(args.get("service") or result.get("service") or "trace")
            items.append({"kind": "trace", "ref": ref, "source": "shop"})
        elif name == "recent_changes":
            items.append(
                {"kind": "change", "ref": str(args.get("service") or "recent"), "source": "shop"}
            )
    return items


def observed_error_rate(exchanges):
    """The checkout error rate a promql call returned, or None if none did."""
    for name, _args, result in exchanges:
        if name == "promql" and result.get("error_rate") is not None:
            try:
                return float(result["error_rate"])
            except (TypeError, ValueError):
                continue
    return None


def _add(found: list[str], name: str) -> None:
    cleaned = (name or "").strip()
    if cleaned and cleaned != "unknown" and cleaned not in found:
        found.append(cleaned)


def suspects_from_exchanges(alert, exchanges) -> list[str]:
    """Services the reads implicated, then the service the alert names.

    An enabled failure flag and a promql fault name both count. The alert
    service is included even when the flag points somewhere else, which is how
    checkout and payment are both investigated.
    """
    found: list[str] = []
    for name, args, result in exchanges:
        if name == "recent_changes":
            for change in result.get("changes") or []:
                if not isinstance(change, dict) or change.get("enabled") is False:
                    continue
                if change.get("kind") == "flag":
                    _add(found, service_for_flag(str(change.get("name") or "")))
                else:
                    _add(found, str(change.get("service") or ""))
        elif name == "promql" and result.get("fault"):
            _add(found, service_for_flag(str(result["fault"])))
        elif name == "traces" and result.get("local_cause") is True:
            _add(found, str(result.get("service") or args.get("service") or ""))
    _add(found, alert_service(alert))
    return found


def triage_from_exchanges(alert, exchanges, tool_calls: int) -> dict:
    """Suspects, severity, and evidence references. Reply text is ignored."""
    services = suspects_from_exchanges(alert, exchanges)
    rate = observed_error_rate(exchanges)
    severity = assign_severity(rate, purchase_path=on_purchase_path(services))
    return {
        "services": services,
        "severity": severity,
        "tool_calls": tool_calls,
        "evidence": evidence_from_exchanges(exchanges),
    }


def _hypothesis(service: str, statement: str, confidence: float, refs=None) -> dict:
    return validate_hypotheses(
        [
            {
                "service": service,
                "statement": statement,
                "evidence_refs": list(refs or [service]),
                "confidence": confidence,
            }
        ]
    )[0]


def _trace_for(service: str, exchanges) -> dict | None:
    found = None
    for name, args, result in exchanges:
        if name != "traces":
            continue
        called = str(args.get("service") or result.get("service") or "")
        if not service or called == service:
            found = result
    return found


def diagnosis_plan_text(plan, hypotheses, services, proposed_action=None) -> str:
    """Plan text for one diagnosis brief.

    The commander opens by tracing the alert service. A payment copy shown that
    step calls ``traces`` on checkout and records no payment verdict. This keeps
    the other reads, names ``traces`` for the services this copy was given, and
    leaves hypotheses about any other service off the brief.
    """
    assigned: list[str] = []
    for service in services or []:
        name = str(service or "").strip()
        if name and name not in assigned:
            assigned.append(name)
    steps: list[dict] = []
    traces_written = False
    for step in (plan or {}).get("steps") or []:
        if not isinstance(step, dict):
            continue
        tool = step.get("tool")
        args = step.get("args") if isinstance(step.get("args"), dict) else {}
        if tool == "traces":
            if traces_written:
                continue
            traces_written = True
            if assigned:
                steps.extend(
                    {"tool": "traces", "args": {"service": service}} for service in assigned
                )
            else:
                steps.append({"tool": "traces", "args": dict(args)})
            continue
        steps.append({"tool": tool, "args": dict(args)})
    if assigned and not traces_written:
        steps.extend({"tool": "traces", "args": {"service": service}} for service in assigned)
    relevant = [
        item
        for item in hypotheses or []
        if isinstance(item, dict) and (not assigned or str(item.get("service") or "") in assigned)
    ]
    shown = {"stage": (plan or {}).get("stage") or "hypotheses", "steps": steps}
    return format_plan(shown, relevant, proposed_action)


def diagnosis_from_exchanges(services, exchanges, tool_calls: int) -> tuple[list[dict], list[dict]]:
    """One report per suspect, plus a hypothesis only when a trace names a fault.

    Confidence stays under the commander's opening hypothesis. A checkout copy
    that finds no local cause returns a denial and no statement. A trace that
    never states ``local_cause`` — Jaeger only returns trace ids — is not a
    denial. ``tool_calls`` is split across the suspects this agent actually
    traced; one suspect keeps the whole count. Those slices sum to that
    agent's calls.
    """
    services = [service for service in services if service]
    if not services:
        return [], []
    traced = [service for service in services if _trace_for(service, exchanges)]
    if len(services) == 1:
        counts = {services[0]: tool_calls}
    else:
        shares = split_budget(tool_calls, len(traced)) if traced else []
        counts = dict(zip(traced, shares, strict=True))
    reports = []
    hypotheses = []
    for service in services:
        trace = _trace_for(service, exchanges) or {}
        report = {
            "service": service,
            "tool_calls": int(counts.get(service) or 0),
        }
        if "local_cause" in trace:
            report["local_cause"] = bool(trace["local_cause"])
        reports.append(report)
        fault = trace.get("fault")
        if fault:
            fault_name = str(fault)
            statement = f"{fault_name} flag" if fault_name.endswith("Failure") else fault_name
            hypotheses.append(_hypothesis(service, statement, DIAGNOSIS_CONFIDENCE))
    return reports, hypotheses


def symptom_of(state) -> str:
    """The alert line lessons and passages are matched against."""
    return alert_summary(state.get("alert"))


def focus_service(state) -> str:
    """The service a runbook lesson has to be about: the top hypothesis, else the alert."""
    top = top_hypothesis(state)
    if top and top.get("service"):
        return str(top["service"])
    return alert_service(state.get("alert"))


def partition_lessons(lessons, *, service: str, symptom: str) -> tuple[list[dict], list[dict]]:
    """Split lessons into those the model may see and those code drops.

    Pending and rejected lessons are dropped even when the preconditions match.
    An approved lesson for another service is dropped too.
    """
    kept = []
    dropped = []
    for lesson in lessons or []:
        if not isinstance(lesson, dict):
            continue
        status = lesson.get("status") or "approved"
        preconditions = lesson.get("preconditions") or {}
        if status in ("rejected", "pending"):
            dropped.append(lesson)
            continue
        if not precondition_matches(preconditions, service=service, symptom=symptom):
            dropped.append(lesson)
            continue
        kept.append(lesson)
    return kept, dropped


def load_lessons(store, *, service: str, symptom: str) -> tuple[list[dict], list[dict]]:
    """Read the lesson store and drop whatever does not apply to this case.

    ``store`` may be a list or an object with ``search(service, symptom, status)``.
    Search is asked for approved lessons. A list is filtered here, so a rejected
    lesson sitting in the list never reaches the model.
    """
    if store is None:
        return [], []
    if isinstance(store, list):
        raw = store
    elif hasattr(store, "search"):
        raw = store.search(service=service, symptom=symptom, status="approved") or []
    else:
        raw = []
    return partition_lessons(raw, service=service, symptom=symptom)


def partition_passages(passages, *, service: str, symptom: str) -> tuple[list[dict], list[dict]]:
    """Keep passages whose service and preconditions fit. Drop the rest."""
    kept = []
    dropped = []
    for passage in passages or []:
        if not isinstance(passage, dict):
            continue
        preconditions = passage.get("preconditions") or {}
        own = str(passage.get("service") or "")
        if preconditions and not precondition_matches(
            preconditions, service=service, symptom=symptom
        ):
            dropped.append(passage)
            continue
        if own and service and own != service:
            dropped.append(passage)
            continue
        kept.append(passage)
    return kept, dropped


def passages_from_exchanges(exchanges) -> list[dict]:
    """Passages returned by search_runbooks, in call order."""
    found = []
    for name, _args, result in exchanges:
        if name == "search_runbooks":
            found.extend(item for item in (result.get("passages") or []) if isinstance(item, dict))
    return found


def runbook_guidance(passages, lessons) -> str:
    """The hand-off remediation is allowed to read. Not the raw retrieval dump."""
    lines = ["Matched runbooks and lessons:"]
    for passage in passages:
        lines.append(
            f"passage {passage.get('id')}: service {passage.get('service')}; fix {passage.get('fix')}"
        )
    for lesson in lessons:
        lines.append(f"lesson {lesson.get('id')}: {lesson.get('text')}")
    if len(lines) == 1:
        lines.append("none")
    lines.append("Propose one action that matches a passage above.")
    return "\n".join(lines)


def _revision_cap(state) -> int:
    cap = state.get("max_revisions")
    return DEFAULT_MAX_REVISIONS if cap is None else int(cap)


def _local_services(reports) -> list[str]:
    found = []
    for report in reports:
        if isinstance(report, dict) and report.get("local_cause") is True:
            service = str(report.get("service") or "")
            if service and service not in found:
                found.append(service)
    return found


def _best_statement(hypotheses, service: str) -> str:
    """Prefer a fault statement already recorded for this service."""
    matches = [
        item
        for item in hypotheses or []
        if isinstance(item, dict) and item.get("service") == service and item.get("statement")
    ]
    if not matches:
        return f"{service} is the root cause"
    matches.sort(key=lambda item: float(item.get("confidence") or 0), reverse=True)
    return str(matches[0]["statement"])


def decide_commander(state) -> dict:
    """The next route and the plan the commander shows before that specialist runs.

    With no diagnosis yet, the alert's own service is the opening hypothesis.
    That is deliberately provisional: a later report can deny it. Once a report
    names a local cause and the top hypothesis is some other service, this adds
    a higher-confidence hypothesis and sends the case back through the monitor.
    The monitor, not this function, decides that the old hypothesis was wrong.

    A failed verification does not jump to the same runbook. ``reinvestigate``
    sends the case through triage again while budget and revisions remain.
    """
    reports = [item for item in (state.get("diagnosis_reports") or []) if isinstance(item, dict)]
    hypotheses = [item for item in (state.get("hypotheses") or []) if isinstance(item, dict)]
    revisions = int(state.get("revisions") or 0)
    cap = _revision_cap(state)
    verification_failed = (state.get("verification") or {}).get("recovered") is False
    failed = verification_failed or bool(state.get("reinvestigate"))
    added: list[dict] = []
    why = ""
    stage = "hypotheses"
    steps: list[dict] = []
    route = "triage"

    if revisions >= cap:
        route, why = "stop", "revision cap"
        stage = "hypotheses"
    elif not reports:
        service = alert_service(state.get("alert"))
        if service:
            added = [
                _hypothesis(
                    service,
                    f"{service} is the root cause",
                    COMMANDER_CONFIDENCE,
                    refs=["alert"],
                )
            ]
        stage = "hypotheses"
        steps = [{"tool": "promql", "args": {"query": "checkout_error_rate"}}]
        if service:
            steps.append({"tool": "traces", "args": {"service": service}})
        route = "triage"
    elif failed:
        if not budget_left(state):
            route, why = "stop", "tool budget exhausted"
        else:
            route = "triage"
        stage = "hypotheses"
    else:
        local = _local_services(reports)
        top = top_hypothesis(state)
        top_service = str(top.get("service") or "") if top else ""
        if local and top_service != local[0]:
            cause = local[0]
            added = [_hypothesis(cause, _best_statement(hypotheses, cause), CORRECTED_CONFIDENCE)]
            # The monitor has to see the corrected top before anyone acts.
            route = "monitor"
            stage = "hypotheses"
        elif local:
            route = "runbook"
            stage = "act"
        elif not budget_left(state):
            route, why = "stop", "tool budget exhausted"
        else:
            route = "triage"

    plan = validate_plan({"stage": stage, "steps": steps})
    visible = format_plan(
        plan,
        hypotheses + added,
        None if route != "runbook" else state.get("proposed_action"),
    )
    return {
        "plan": plan,
        "route": route,
        "hypotheses": added,
        "visible": visible,
        "why": why,
    }
