import time

from fastapi.testclient import TestClient
from oncall_service.main import create_app


def _wait_for_status(client: TestClient, incident_id: str, expected: str) -> dict:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        response = client.get(f"/incidents/{incident_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] == expected:
            return payload
        time.sleep(0.01)
    raise AssertionError(f"incident {incident_id} did not reach {expected}")


def test_health_and_console_are_served():
    with TestClient(create_app(demo_delay=0)) as client:
        health = client.get("/healthz")
        page = client.get("/")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert page.status_code == 200
    assert "Incident Console" in page.text
    assert "No incident selected" in page.text


def test_demo_streams_to_approval_and_exact_hash_recovers():
    with TestClient(create_app(demo_delay=0)) as client:
        started = client.post("/api/demo")
        assert started.status_code == 202
        incident_id = started.json()["incident_id"]
        waiting = _wait_for_status(client, incident_id, "AWAITING_APPROVAL")

        assert waiting["state"]["hypotheses"][0]["service"] == "payment"
        assert len(waiting["state"]["evidence"]) == 2
        assert waiting["approval"]["payload_hash"]
        assert waiting["events"][-1]["type"] == "approval.requested"

        stale = client.post(
            f"/incidents/{incident_id}/approve",
            json={"decision": "approve", "payload_hash": "stale"},
        )
        assert stale.status_code == 409

        approved = client.post(
            f"/incidents/{incident_id}/approve",
            json={
                "decision": "approve",
                "payload_hash": waiting["approval"]["payload_hash"],
            },
        )
        assert approved.status_code == 200
        recovered = _wait_for_status(client, incident_id, "RECOVERED")

    assert recovered["terminal"]
    assert recovered["state"]["verification"]["recovered"] is True
    assert recovered["system_run"]["verified_recovery"] is True
    assert recovered["events"][-1]["type"] == "verification.recovered"


def test_demo_rejection_is_terminal_and_executes_nothing():
    with TestClient(create_app(demo_delay=0)) as client:
        incident_id = client.post("/api/demo").json()["incident_id"]
        _wait_for_status(client, incident_id, "AWAITING_APPROVAL")
        response = client.post(
            f"/incidents/{incident_id}/approve",
            json={"decision": "reject"},
        )
        assert response.status_code == 200
        rejected = _wait_for_status(client, incident_id, "REJECTED")

    assert rejected["terminal"]
    assert rejected["approval"] is None
    assert rejected["system_run"] is None


def test_alert_contract_rejects_incomplete_payload_before_agent_start():
    with TestClient(create_app(demo_delay=0)) as client:
        response = client.post("/alerts", json={"status": "firing"})

    assert response.status_code == 422
    assert "missing fields" in response.json()["detail"]
