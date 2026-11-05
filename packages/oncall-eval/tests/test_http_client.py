from urllib.parse import parse_qs, urlparse

import pytest
from oncall_eval.http_client import IncidentApiClient, IncidentApiError


def _system_run() -> dict:
    action = {
        "action": "set_flag",
        "args": {"flag": "paymentFailure", "variant": "off"},
    }
    return {
        "root_cause_services": ["payment"],
        "proposed_action": action,
        "executed_actions": [action],
        "tool_calls": 4,
        "tokens": 100,
        "usd": 0.01,
        "verified_recovery": True,
        "rolled_back": False,
        "model_ids": {"solo": "test-model"},
    }


def _alert() -> dict:
    return {
        "status": "firing",
        "labels": {"alertname": "CheckoutErrorRateHigh", "service": "checkout"},
        "annotations": {"summary": "Checkout failures"},
        "startsAt": "2026-11-02T00:00:00Z",
    }


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_client_submits_alert_polls_approves_exact_hash_and_returns_evidence():
    responses = iter(
        [
            {"status": "RUNNING"},
            {"status": "RUNNING", "approval": {"payload_hash": "abc123"}},
            {"status": "RECOVERED", "system_run": _system_run()},
        ]
    )
    requests = []

    def transport(method: str, url: str, body, timeout: float):
        requests.append((method, url, body, timeout))
        return next(responses)

    clock = FakeClock()
    client = IncidentApiClient(
        "http://incident:8000/",
        transport=transport,
        clock=clock,
        sleep=clock.sleep,
    )

    result = client.run(
        _alert(),  # type: ignore[arg-type]
        incident_id="eval/run 1",
        config_id="c1_single",
        tool_budget=5,
    )

    assert result == _system_run()
    assert requests[0][0] == "POST"
    assert requests[0][2] == _alert()
    query = parse_qs(urlparse(requests[0][1]).query)
    assert query == {
        "incident_id": ["eval/run 1"],
        "config_id": ["c1_single"],
        "tool_budget": ["5"],
    }
    assert requests[1][0:3] == (
        "GET",
        "http://incident:8000/incidents/eval%2Frun%201",
        None,
    )
    assert requests[2][0:3] == (
        "POST",
        "http://incident:8000/incidents/eval%2Frun%201/approve",
        {"decision": "approve", "payload_hash": "abc123"},
    )


def test_client_rejects_repeated_approval_hash():
    responses = iter(
        [
            {"approval": {"payload_hash": "same"}},
            {"approval": {"payload_hash": "same"}},
        ]
    )
    client = IncidentApiClient(
        transport=lambda *_: next(responses),
    )

    with pytest.raises(IncidentApiError, match="already-approved"):
        client.run(_alert(), incident_id="run", config_id="c1_single", tool_budget=5)  # type: ignore[arg-type]


def test_client_times_out_nonterminal_incident():
    clock = FakeClock()
    client = IncidentApiClient(
        run_timeout_seconds=2,
        poll_interval_seconds=1,
        transport=lambda *_: {"status": "RUNNING"},
        clock=clock,
        sleep=clock.sleep,
    )

    with pytest.raises(IncidentApiError, match="did not terminate"):
        client.run(_alert(), incident_id="run", config_id="c1_single", tool_budget=5)  # type: ignore[arg-type]


def test_client_requires_terminal_system_evidence():
    client = IncidentApiClient(transport=lambda *_: {"status": "RECOVERED"})

    with pytest.raises(IncidentApiError, match="no system_run"):
        client.run(_alert(), incident_id="run", config_id="c1_single", tool_budget=5)  # type: ignore[arg-type]
