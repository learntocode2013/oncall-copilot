"""Span-metric triage and diagnosis: failure versus unreachable. No network."""

import threading

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from oncall_graph.briefs import alert_service
from oncall_graph.config import SPECIALIST_MODEL
from oncall_graph.evidence import (
    CLIENT_CHARGE,
    ERROR_BY_SERVICE,
    MAX_CONFIDENCE,
    P95_BY_SERVICE,
    SERVER_CHARGE,
    UNREACHABLE,
    diagnose,
    error_query,
    ground_suspects,
    has_series,
    missing_reads,
    score_candidate,
)
from oncall_graph.meter import reset_log
from oncall_graph.stubs import Shop
from oncall_graph.topologies.c1_single import interrupt_value
from oncall_graph.topologies.c2_team import Team

CHECKOUT_ALERT = {
    "status": "firing",
    "labels": {"alertname": "CheckoutErrorRateHigh", "service": "checkout"},
    "annotations": {"summary": "checkout error rate above 5% for five minutes"},
    "startsAt": "2026-11-06T00:00:00Z",
}


def _series(value, **labels):
    return {"metric": labels, "value": [0, str(value)]}


def _prom(query, *series):
    return ("promql", {"query": query}, {"query": query, "series": list(series)})


def _exchanges(*, errors, server_calls, client_errors, p95=None, users=()):
    found = [
        _prom(ERROR_BY_SERVICE, *(_series(v, service_name=k) for k, v in errors.items())),
        _prom(P95_BY_SERVICE, *(_series(v, service_name=k) for k, v in (p95 or {}).items())),
        _prom(CLIENT_CHARGE, _series(client_errors)),
        _prom(SERVER_CHARGE, *([_series(server_calls)] if server_calls else [])),
        (
            "traces",
            {"service": "checkout"},
            {
                "service": "checkout",
                "trace_ids": ["t1"],
                "users": list(users),
                "error_trace_ids": ["t1"],
            },
        ),
    ]
    return found


@pytest.fixture(autouse=True)
def _clear_log():
    reset_log()
    yield
    reset_log()


def test_the_scripted_error_rate_answer_is_not_series():
    assert not has_series([("promql", {"query": "checkout_error_rate"}, {"error_rate": 0.08})])
    assert ground_suspects("checkout", [("promql", {"query": "x"}, {"error_rate": 0.08})]) == []


def test_payment_failure_blames_payment_and_refutes_unreachable():
    exchanges = _exchanges(
        errors={"checkout": 0.5, "payment": 0.9}, server_calls=1.3, client_errors=1.2
    )
    candidates = ground_suspects("checkout", exchanges)
    kinds = {(item["service"], item["kind"]) for item in candidates}
    assert kinds == {("checkout", "errors"), ("payment", "errors"), ("checkout", "unreachable")}

    reports, hypotheses, _users, ruled_out = diagnose(
        ["checkout", "payment"], candidates, exchanges, 4
    )
    verdicts = {item["service"]: item.get("local_cause") for item in reports}
    assert verdicts == {"checkout": False, "payment": True}
    assert [item["statement"] for item in hypotheses] == ["payment is returning errors"]
    assert any(line.startswith(UNREACHABLE) for line in ruled_out)
    assert any(line.startswith("checkout is returning errors") for line in ruled_out)


def test_payment_unreachable_blames_payment_from_the_checkout_copy():
    exchanges = _exchanges(
        errors={"checkout": 0.6}, server_calls=0, client_errors=1.1, users=("user-17",)
    )
    candidates = ground_suspects("checkout", exchanges)
    assert {item["service"] for item in candidates} == {"checkout"}

    reports, hypotheses, users, _ruled_out = diagnose(["checkout"], candidates, exchanges, 5)
    assert {"service": "payment", "local_cause": True, "tool_calls": 0} in reports
    assert next(item for item in reports if item["service"] == "checkout")["local_cause"] is False
    assert hypotheses[0]["service"] == "payment"
    assert hypotheses[0]["statement"] == UNREACHABLE
    assert hypotheses[0]["confidence"] <= MAX_CONFIDENCE
    assert users == ["user-17"]
    for item in hypotheses:
        assert all(":" in ref or ref.isidentifier() for ref in item["evidence_refs"])
        assert "user-17" not in " ".join(item["evidence_refs"])


def test_a_healthy_shop_grounds_only_the_alert_service_and_finds_no_cause():
    exchanges = _exchanges(
        errors={"checkout": 0.0, "payment": 0.0}, server_calls=1.3, client_errors=0.0
    )
    candidates = ground_suspects("checkout", exchanges)
    assert [(item["service"], item["kind"]) for item in candidates] == [
        ("checkout", "errors"),
        ("checkout", "unreachable"),
    ]
    reports, hypotheses, _users, ruled_out = diagnose(["checkout"], candidates, exchanges, 4)
    assert reports == [{"service": "checkout", "tool_calls": 4, "local_cause": False}]
    assert hypotheses == []
    assert len(ruled_out) == 2


def test_an_unread_query_is_no_verdict_not_a_denial():
    candidate = {"service": "payment", "statement": "payment is returning errors", "kind": "errors"}
    scored = score_candidate(candidate, [])
    assert scored["measured"] is False
    reports, _hypotheses, _users, _ruled_out = diagnose(["payment"], [candidate], [], 0)
    assert reports == [{"service": "payment", "tool_calls": 0}]
    assert f"promql query={error_query('payment')}" in missing_reads(["payment"], [candidate], [])


def test_nan_in_the_last_step_is_not_a_reading():
    exchanges = [_prom(error_query("payment"), _series("NaN"))]
    candidate = {"service": "payment", "statement": "payment is returning errors", "kind": "errors"}
    assert score_candidate(candidate, exchanges)["measured"] is False


def test_alert_service_reads_the_span_metric_label():
    alert = {"labels": {"alertname": "x", "service_name": "checkout"}, "annotations": {}}
    assert alert_service(alert) == "checkout"
    assert (
        alert_service({**alert, "labels": {"service": "payment", "service_name": "checkout"}})
        == "payment"
    )


def _ai(content="", tool_calls=None):
    return AIMessage(
        content=content,
        tool_calls=tool_calls or [],
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )


def _line(text, prefix):
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return ""


class SpanModel:
    """Triage runs the span queries. Diagnosis traces once, then runs what it is told it owes."""

    def __init__(self):
        self.model_id = SPECIALIST_MODEL
        self.prompts = []
        self._lock = threading.Lock()
        self._ids = 0

    def bind_tools(self, tools):
        return self

    def _call(self, name, args):
        with self._lock:
            self._ids += 1
            return {"name": name, "args": args, "id": f"c{self._ids}", "type": "tool_call"}

    def invoke(self, messages):
        text = "\n".join(str(getattr(m, "content", "")) for m in messages)
        has_tool = any(isinstance(m, ToolMessage) for m in messages)
        with self._lock:
            self.prompts.append(text)
        if "Agent: commander" in text:
            return _ai("plan acknowledged")
        if "Agent: triage" in text:
            if has_tool:
                return _ai("reads done")
            queries = (ERROR_BY_SERVICE, P95_BY_SERVICE, CLIENT_CHARGE, SERVER_CHARGE)
            return _ai(
                "",
                [self._call("promql", {"query": q}) for q in queries]
                + [
                    self._call("recent_changes", {"service": ""}),
                    self._call("traces", {"service": "checkout"}),
                    self._call("log_search", {"service": "checkout"}),
                ],
            )
        if "Agent: diagnosis" in text:
            if has_tool:
                return _ai("tested")
            if "Call these reads before you finish:" in text:
                calls = []
                for line in text.split("Call these reads before you finish:", 1)[1].splitlines():
                    if line.startswith("promql query="):
                        calls.append(self._call("promql", {"query": line[len("promql query=") :]}))
                    elif line.startswith("traces service="):
                        calls.append(self._call("traces", {"service": line.split("=", 1)[1]}))
                    elif line.startswith("log_search service="):
                        calls.append(self._call("log_search", {"service": line.split("=", 1)[1]}))
                return _ai("", calls)
            service = _line(text, "Service: ")
            return _ai("", [self._call("traces", {"service": service})])
        if "Agent: runbook" in text:
            if has_tool:
                return _ai("runbook read")
            return _ai("", [self._call("search_runbooks", {"symptom": "checkout error rate"})])
        if "Agent: remediation" in text:
            if has_tool or "Verification: recovered=False" in text:
                return _ai("proposed")
            flag = "paymentUnreachable" if UNREACHABLE in text else "paymentFailure"
            return _ai(
                "",
                [
                    self._call(
                        "set_flag",
                        {"flag": flag, "variant": "off", "expected_effect": "checkout recovers"},
                    )
                ],
            )
        if "Agent: comms" in text:
            if has_tool:
                return _ai("drafted")
            return _ai("", [self._call("status_draft", {"note": "recovered"})])
        return _ai("ok")


def _texts(state):
    return [item["text"] for item in state.get("transcript") or []]


@pytest.mark.parametrize(
    ("fault", "statement"),
    [
        ("paymentUnreachable", UNREACHABLE),
        ("paymentFailure", "payment is returning errors"),
    ],
)
def test_the_team_tells_the_two_payment_faults_apart(fault, statement):
    shop = Shop(fault=fault)
    model = SpanModel()
    team = Team(model=model, shop=shop, tool_budget=20)
    paused, _measured = team.run(CHECKOUT_ALERT, incident_id=fault)

    assert "checkout" in paused["scope"]["services"]
    assert "payment" in paused["scope"]["services"]
    top = max(paused["hypotheses"], key=lambda item: item["confidence"])
    assert top["service"] == "payment"
    assert top["statement"] == statement
    checkout = [r for r in paused["diagnosis_reports"] if r["service"] == "checkout"]
    assert checkout and all(r.get("local_cause") is False for r in checkout)
    assert any(
        r["service"] == "payment" and r.get("local_cause") for r in paused["diagnosis_reports"]
    )
    assert "monitor -> replan" in _texts(paused)

    owed = [p for p in model.prompts if "Call these reads before you finish:" in p]
    assert owed, "diagnosis was not asked for the reads it skipped"
    assert all("Tool calls allowed: 0." not in p for p in owed)

    assert paused["proposed_action"]["args"] == {"flag": fault, "variant": "off"}
    assert shop.writes == []
    if fault == "paymentUnreachable":
        assert "triage users user-17, user-42" in _texts(paused)
    else:
        assert all(item["statement"] != UNREACHABLE for item in paused["hypotheses"])
        assert any("ruled out" in line and UNREACHABLE in line for line in _texts(paused))

    intr = interrupt_value(paused)
    done, _measured = team.run(
        resume={"decision": "approve", "payload_hash": intr["payload_hash"]}, incident_id=fault
    )
    assert done["status"] == "RECOVERED"
    assert shop.writes == [{"tool": "set_flag", "args": {"flag": fault, "variant": "off"}}]
    assert done["tool_calls"] <= 20
