"""Span-metric evidence: suspects for triage, scores for diagnosis.

The model chooses which reads to run. These functions read the results back
from the ``(tool, args, result)`` exchanges and decide in code. A candidate is
what triage asks diagnosis to test; a score is what diagnosis found.

Every function here returns nothing on the scripted fixture's ``error_rate``
and ``fault`` answers, which carry no ``series``. ``has_series`` is the switch
the team uses to keep that older path.
"""

from __future__ import annotations

import math

from .briefs import split_budget

ERROR_BY_SERVICE = (
    "sum by (service_name) (rate(traces_span_metrics_calls_total"
    '{span_kind="SPAN_KIND_SERVER", status_code="STATUS_CODE_ERROR"}[5m]))'
    " / sum by (service_name) (rate(traces_span_metrics_calls_total"
    '{span_kind="SPAN_KIND_SERVER"}[5m]))'
)
P95_BY_SERVICE = (
    "histogram_quantile(0.95, sum by (service_name, le) (rate("
    'traces_span_metrics_duration_milliseconds_bucket{span_kind="SPAN_KIND_SERVER"}[5m])))'
)
CLIENT_CHARGE = (
    'sum(rate(traces_span_metrics_calls_total{service_name="checkout", '
    'span_kind="SPAN_KIND_CLIENT", span_name="oteldemo.PaymentService/Charge", '
    'status_code="STATUS_CODE_ERROR"}[5m]))'
)
SERVER_CHARGE = (
    'sum(rate(traces_span_metrics_calls_total{service_name="payment", '
    'span_kind="SPAN_KIND_SERVER", span_name="oteldemo.PaymentService/Charge"}[5m]))'
)

ERROR_THRESHOLD = 0.05
LATENCY_MS = 500.0

#: Every candidate starts here. A read that supports it adds; one that refutes it subtracts.
BASE_CONFIDENCE = 0.2
#: Under the commander's 0.9 opening and 0.95 correction, so a score never outranks them.
MAX_CONFIDENCE = 0.85
#: At or above this, the service's report says the fault is local.
LOCAL_CAUSE_LINE = 0.5
#: Checkout's own errors, once payment's Charge errors explain them.
DOWNSTREAM_CAP = 0.45

UNREACHABLE = "payment is unreachable from checkout"
#: The checkout copy tests the unreachable claim; the cause it names is payment.
UNREACHABLE_TESTED_BY = "checkout"
UNREACHABLE_CAUSE = "payment"


def error_query(service: str) -> str:
    return (
        "sum(rate(traces_span_metrics_calls_total"
        f'{{service_name="{service}", span_kind="SPAN_KIND_SERVER", '
        'status_code="STATUS_CODE_ERROR"}[5m]))'
        " / sum(rate(traces_span_metrics_calls_total"
        f'{{service_name="{service}", span_kind="SPAN_KIND_SERVER"}}[5m]))'
    )


def p95_query(service: str) -> str:
    return (
        "histogram_quantile(0.95, sum by (le) (rate("
        "traces_span_metrics_duration_milliseconds_bucket"
        f'{{service_name="{service}", span_kind="SPAN_KIND_SERVER"}}[5m])))'
    )


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _series(result) -> list[tuple[dict, float]]:
    """``(labels, last value)`` per series, NaN and missing points dropped."""
    rows = result.get("series") if isinstance(result, dict) else result
    values = []
    for series in rows or []:
        if not isinstance(series, dict):
            continue
        raw = series.get("value") or [None, None]
        number = _finite(raw[1] if isinstance(raw, (list, tuple)) and len(raw) > 1 else None)
        if number is not None:
            values.append((series.get("metric") or {}, number))
    return values


def _promql(exchanges) -> list[tuple[str, dict]]:
    found = []
    for name, args, result in exchanges or []:
        if name != "promql" or not isinstance(result, dict):
            continue
        query = str(args.get("query") or result.get("query") or "")
        found.append((query, result))
    return found


def has_series(exchanges) -> bool:
    """True when a promql answer came back in the live client's ``series`` shape."""
    return any("series" in result for _query, result in _promql(exchanges))


def _service_number(result, service: str):
    values = _series(result)
    matched = [value for metric, value in values if metric.get("service_name") == service]
    if matched:
        return matched[-1]
    rows = result.get("series") if isinstance(result, dict) else result
    if not rows:
        return None
    if len(values) == 1 and not values[0][0].get("service_name"):
        return values[0][1]
    return None


def _is_error_ratio(query: str) -> bool:
    return (
        "STATUS_CODE_ERROR" in query
        and "histogram_quantile" not in query
        and "SPAN_KIND_CLIENT" not in query
    )


def _mentions(query: str, service: str) -> bool:
    return f'service_name="{service}"' in query or "by (service_name" in query


def error_ratio_for(exchanges, service: str):
    """The last server error ratio read for ``service``, or None if none was."""
    found = None
    for query, result in _promql(exchanges):
        if not _is_error_ratio(query) or not _mentions(query, service):
            continue
        value = _service_number(result, service)
        if value is not None:
            found = value
    return found


def p95_for(exchanges, service: str):
    """The last server p95 in milliseconds read for ``service``, or None."""
    found = None
    for query, result in _promql(exchanges):
        if "histogram_quantile" not in query or not _mentions(query, service):
            continue
        value = _service_number(result, service)
        if value is not None:
            found = value
    return found


def _single(result) -> float:
    """A ``sum(...)`` answer. An empty result is a zero rate, not a missing read."""
    values = _series(result)
    return values[0][1] if values else 0.0


def unreachable_pair(exchanges):
    """``(checkout Charge client errors, payment Charge server calls)``; None where unread."""
    client = server = None
    for query, result in _promql(exchanges):
        if "PaymentService/Charge" not in query:
            continue
        if "SPAN_KIND_CLIENT" in query and "STATUS_CODE_ERROR" in query:
            client = _single(result)
        elif "SPAN_KIND_SERVER" in query and "STATUS_CODE_ERROR" not in query:
            server = _single(result)
    return client, server


def _calls(exchanges, tool: str, service: str) -> list[dict]:
    return [
        result
        for name, args, result in exchanges or []
        if name == tool
        and isinstance(result, dict)
        and str(args.get("service") or result.get("service") or "") == service
    ]


def users_and_traces(exchanges, service: str) -> tuple[list[str], list[str]]:
    users: list[str] = []
    traces: list[str] = []
    for result in _calls(exchanges, "traces", service):
        users.extend(str(item) for item in result.get("users") or [])
        traces.extend(str(item) for item in result.get("error_trace_ids") or [])
    return list(dict.fromkeys(users)), list(dict.fromkeys(traces))


def error_log_lines(exchanges, service: str) -> list[str]:
    lines = []
    for result in _calls(exchanges, "log_search", service):
        for line in result.get("lines") or []:
            text = str(line).lower()
            if "error" in text or "warn" in text or "fail" in text or "refused" in text:
                lines.append(str(line))
    return lines


def all_users(exchanges) -> list[str]:
    users: list[str] = []
    for name, _args, result in exchanges or []:
        if name == "traces" and isinstance(result, dict):
            users.extend(str(item) for item in result.get("users") or [])
    return list(dict.fromkeys(users))


def _candidate(service: str, statement: str, kind: str, note: str) -> dict:
    return {"service": service, "statement": statement, "kind": kind, "evidence": [note]}


def ground_suspects(
    alert_service: str,
    exchanges,
    *,
    error_threshold: float = ERROR_THRESHOLD,
    latency_ms: float = LATENCY_MS,
) -> list[dict]:
    """Candidates for every service the span metrics implicate, plus the alert's.

    A service is grounded when its error ratio or p95 crosses the line. The
    unreachable candidate is added whenever checkout or payment is involved,
    and the checkout copy tests it. Without ``series`` answers this is empty.
    """
    if not has_series(exchanges):
        return []
    grounded: set[str] = set()
    for query, result in _promql(exchanges):
        for metric, value in _series(result):
            name = metric.get("service_name")
            if not name:
                continue
            slow = "histogram_quantile" in query and value >= latency_ms
            if slow or (_is_error_ratio(query) and value >= error_threshold):
                grounded.add(name)
    if alert_service:
        grounded.add(alert_service)

    kept: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(item: dict) -> None:
        key = (item["service"], item["statement"].lower())
        if key not in seen:
            kept.append(item)
            seen.add(key)

    for name in sorted(grounded):
        ratio = error_ratio_for(exchanges, name)
        if ratio is not None and ratio >= error_threshold:
            add(
                _candidate(
                    name, f"{name} is returning errors", "errors", f"triage error_ratio={ratio:.3f}"
                )
            )
        p95 = p95_for(exchanges, name)
        if p95 is not None and p95 >= latency_ms:
            add(
                _candidate(
                    name, f"{name} latency is elevated", "latency", f"triage p95_ms={p95:.0f}"
                )
            )
    if alert_service and not any(item["service"] == alert_service for item in kept):
        add(
            _candidate(
                alert_service,
                f"{alert_service} is returning errors",
                "errors",
                "triage alert service",
            )
        )
    names = {item["service"] for item in kept} | ({alert_service} if alert_service else set())
    if names & {"checkout", "payment"}:
        add(_candidate(UNREACHABLE_TESTED_BY, UNREACHABLE, "unreachable", "triage suspect"))
    return kept


def candidate_services(candidates) -> list[str]:
    found: list[str] = []
    for item in candidates or []:
        name = str(item.get("service") or "")
        if name and name not in found:
            found.append(name)
    return found


def _clamp(value: float) -> float:
    return round(max(0.0, min(MAX_CONFIDENCE, value)), 3)


def score_candidate(
    candidate: dict,
    exchanges,
    *,
    error_threshold: float = ERROR_THRESHOLD,
    latency_ms: float = LATENCY_MS,
) -> dict:
    """Confidence, evidence refs, and the users seen, for one candidate.

    ``measured`` is False when the deciding read never ran, so the caller can
    report no verdict rather than a denial. Refs name the read, not its payload.
    """
    service = str(candidate.get("service") or "")
    kind = candidate.get("kind") or "errors"
    confidence = BASE_CONFIDENCE
    notes: list[str] = []
    refs: list[str] = []
    measured = True
    if kind == "unreachable":
        client, server = unreachable_pair(exchanges)
        if client is None or server is None:
            measured = False
            notes.append("unreachable queries were not run")
        else:
            refs.extend(
                ["promql:checkout_charge_client_errors", "promql:payment_charge_server_calls"]
            )
            notes.append(
                f"checkout Charge client errors={client}, payment Charge server calls={server}"
            )
            confidence += 0.7 if client > 0 and server == 0 else -0.2
    elif kind == "latency":
        p95 = p95_for(exchanges, service)
        if p95 is None:
            measured = False
            notes.append("p95 was not queried")
        else:
            refs.append(f"promql:p95:{service}")
            notes.append(f"p95_ms={p95:.0f}")
            confidence += 0.45 if p95 >= latency_ms else -0.15
    else:
        ratio = error_ratio_for(exchanges, service)
        if ratio is None:
            measured = False
            notes.append("error ratio was not queried")
        else:
            refs.append(f"promql:error_ratio:{service}")
            notes.append(f"error_ratio={ratio:.3f}")
            confidence += 0.5 if ratio >= error_threshold else -0.2
    users, traces = users_and_traces(exchanges, service)
    logs = error_log_lines(exchanges, service)
    if users:
        confidence += 0.1
    if traces:
        refs.append(f"trace:{service}")
        notes.append(f"error_traces={len(traces)}")
        confidence += 0.05
    if logs:
        refs.append(f"log_query:{service}")
        notes.append(f"error_log_lines={len(logs)}")
        confidence += 0.1
    return {
        "confidence": _clamp(confidence),
        "refs": refs or [service],
        "users": users,
        "notes": notes,
        "measured": measured,
    }


def checkout_errors_are_downstream(exchanges) -> bool:
    """Checkout's Charge client spans are erroring, so payment explains checkout's errors."""
    client, _server = unreachable_pair(exchanges)
    return bool(client and client > 0)


def span_overview_missing(exchanges) -> list[str]:
    """The per-service overview queries that did not come back with series.

    A query Prometheus rejects answers with ``error`` and no ``series``, so it
    does not count: triage is handed the exact text to call again.
    """
    answered = [
        query
        for query, result in _promql(exchanges)
        if isinstance(result, dict) and "series" in result
    ]
    missing = []
    if not any(
        "sum by (service_name)" in query and "STATUS_CODE_ERROR" in query for query in answered
    ):
        missing.append(ERROR_BY_SERVICE)
    if not any("histogram_quantile" in query and "by (service_name" in query for query in answered):
        missing.append(P95_BY_SERVICE)
    return missing


def missing_reads(services, candidates, exchanges) -> list[str]:
    """The reads a copy still owes before its candidates can be scored."""
    queries = [query for query, _result in _promql(exchanges)]
    kinds = {item.get("kind") for item in candidates or []}
    missing: list[str] = []
    if "unreachable" in kinds:
        if not any("SPAN_KIND_CLIENT" in q and "PaymentService/Charge" in q for q in queries):
            missing.append(f"promql query={CLIENT_CHARGE}")
        if not any(
            "SPAN_KIND_SERVER" in q
            and "PaymentService/Charge" in q
            and "STATUS_CODE_ERROR" not in q
            for q in queries
        ):
            missing.append(f"promql query={SERVER_CHARGE}")
    for service in services or []:
        mine = {item.get("kind") for item in candidates or [] if item.get("service") == service}
        if "latency" in mine and p95_for(exchanges, service) is None:
            missing.append(f"promql query={p95_query(service)}")
        if "errors" in mine and error_ratio_for(exchanges, service) is None:
            missing.append(f"promql query={error_query(service)}")
        if not _calls(exchanges, "traces", service):
            missing.append(f"traces service={service}")
        if not _calls(exchanges, "log_search", service):
            missing.append(f"log_search service={service}")
    return missing


def diagnose(
    services, candidates, exchanges, tool_calls: int
) -> tuple[list[dict], list[dict], list[str], list[str]]:
    """Reports, supported hypotheses ranked by confidence, users seen, and claims ruled out.

    Only a hypothesis at or above ``LOCAL_CAUSE_LINE`` goes on the shared
    list: every later brief renders that list, and a refuted claim there reads
    to remediation like a lead. A service whose deciding reads never ran gets a
    report with no ``local_cause``, which the monitor does not read as a
    denial. Checkout's errors are not local when its Charge client spans are
    erroring. A confirmed unreachable claim is a payment report and hypothesis.
    """
    services = [service for service in services or [] if service]
    if not services:
        return [], [], [], []
    shares = split_budget(tool_calls, len(services))
    downstream = checkout_errors_are_downstream(exchanges)
    reports: list[dict] = []
    hypotheses: list[dict] = []
    users: list[str] = []
    unreachable_confirmed = False
    for index, service in enumerate(services):
        report: dict = {"service": service, "tool_calls": int(shares[index])}
        verdicts = []
        for candidate in candidates or []:
            if candidate.get("service") != service:
                continue
            scored = score_candidate(candidate, exchanges)
            users.extend(item for item in scored["users"] if item not in users)
            confidence = scored["confidence"]
            if candidate.get("kind") == "unreachable":
                if scored["measured"] and confidence >= LOCAL_CAUSE_LINE:
                    unreachable_confirmed = True
                hypotheses.append(
                    {
                        "service": UNREACHABLE_CAUSE,
                        "statement": UNREACHABLE,
                        "evidence_refs": scored["refs"],
                        "confidence": confidence,
                    }
                )
                continue
            if service == "checkout" and downstream:
                confidence = min(confidence, DOWNSTREAM_CAP)
            if scored["measured"]:
                verdicts.append(confidence)
            hypotheses.append(
                {
                    "service": service,
                    "statement": str(candidate.get("statement") or f"{service} is the root cause"),
                    "evidence_refs": scored["refs"],
                    "confidence": confidence,
                }
            )
        if verdicts:
            report["local_cause"] = max(verdicts) >= LOCAL_CAUSE_LINE
        reports.append(report)
    if unreachable_confirmed and UNREACHABLE_CAUSE not in services:
        reports.append({"service": UNREACHABLE_CAUSE, "local_cause": True, "tool_calls": 0})
    elif unreachable_confirmed:
        for report in reports:
            if report["service"] == UNREACHABLE_CAUSE:
                report["local_cause"] = True
    hypotheses.sort(key=lambda item: item["confidence"], reverse=True)
    supported = [item for item in hypotheses if item["confidence"] >= LOCAL_CAUSE_LINE]
    ruled_out = [
        f"{item['statement']} ({item['confidence']})"
        for item in hypotheses
        if item["confidence"] < LOCAL_CAUSE_LINE
    ]
    return reports, supported, users, ruled_out
