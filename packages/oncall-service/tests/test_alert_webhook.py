"""A Grafana or Alertmanager group starts the solo agent once per firing alert."""

import time

import pytest
from fastapi.testclient import TestClient
from oncall_service.alerts import AlertPayloadError, firing_alerts
from oncall_service.main import create_app

FIRING = {
    "receiver": "oncall-copilot",
    "status": "firing",
    "alerts": [
        {
            "status": "firing",
            "labels": {
                "alertname": "CheckoutErrorRateHigh",
                "service": "checkout",
                "severity": "critical",
            },
            "annotations": {"summary": "Checkout error rate above 5% for five minutes"},
            "startsAt": "2026-11-03T12:00:00Z",
            "fingerprint": "checkout-422",
        }
    ],
}


def test_firing_group_keeps_the_alert_contract_fields():
    alert, key = firing_alerts(FIRING)[0]

    assert key == "checkout-422"
    assert alert["status"] == "firing"
    assert alert["labels"]["alertname"] == "CheckoutErrorRateHigh"
    assert alert["labels"]["service"] == "checkout"
    assert alert["labels"]["severity"] == "critical"
    assert alert["startsAt"] == "2026-11-03T12:00:00Z"


def test_resolved_alerts_do_not_start_an_investigation():
    body = {
        "alerts": [
            {
                "status": "resolved",
                "labels": {"alertname": "CheckoutErrorRateHigh"},
                "annotations": {},
                "startsAt": "2026-11-03T12:00:00Z",
            }
        ]
    }

    assert firing_alerts(body) == []


def test_firing_alert_without_a_name_is_rejected():
    body = {"alerts": [{"status": "firing", "labels": {}, "annotations": {}, "startsAt": "t"}]}

    with pytest.raises(AlertPayloadError, match="alertname"):
        firing_alerts(body)


def test_notification_starts_one_agent_and_a_repeat_does_not(monkeypatch):
    calls: list[str] = []

    async def fake_run(store, incident, resume=None):
        calls.append(incident.incident_id)
        incident.status = "INVESTIGATING"
        await store.emit(incident, "agent.started", "patched agent")

    monkeypatch.setattr("oncall_service.main._run_real", fake_run)
    with TestClient(create_app(demo_delay=0)) as client:
        first = client.post("/alerts", json=FIRING)
        assert first.status_code == 202
        started = first.json()["incidents"]
        assert len(started) == 1
        assert started[0]["mode"] == "hybrid-live"
        assert started[0]["alert"]["labels"]["alertname"] == "CheckoutErrorRateHigh"
        incident_id = started[0]["incident_id"]

        deadline = time.monotonic() + 2
        while incident_id not in calls and time.monotonic() < deadline:
            time.sleep(0.01)

        second = client.post("/alerts", json=FIRING)

    assert second.status_code == 202
    assert second.json()["incidents"][0]["incident_id"] == incident_id
    assert calls == [incident_id]
