"""The lab arms one manifest flag and pages the agent with the symptom only."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from oncall_lab.catalog import CatalogError, load_catalog
from oncall_lab.main import create_app
from oncall_lab.paging import notification

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "eval/faults/manifest.yaml"
SHOP_FLAGS = ROOT / "deploy/flagd/demo.flagd.json"

FORBIDDEN = ("payment", "paymentFailure", "set_flag", "root_cause", "100%")


def _flags(tmp_path: Path) -> Path:
    path = tmp_path / "demo.flagd.json"
    path.write_text(
        json.dumps(
            {
                "flags": {
                    "paymentFailure": {
                        "defaultVariant": "off",
                        "variants": {"off": 0, "100%": 1},
                    },
                    "adFailure": {
                        "defaultVariant": "off",
                        "variants": {"off": False, "on": True},
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def _client(
    tmp_path: Path,
    poster,
    flag_path: Path | None = None,
    manifest: Path = MANIFEST,
    symptoms=None,
):
    calls: list[tuple[str, dict]] = []

    def record(url: str, body: dict) -> dict:
        calls.append((url, body))
        return poster(url, body)

    app = create_app(
        manifest_path=manifest,
        flag_path=flag_path or _flags(tmp_path),
        alerts_url="http://oncall.test/alerts",
        console_url="http://localhost:8000/",
        poster=record,
        clock=lambda: "2026-11-03T12:00:00Z",
        symptoms=symptoms,
    )
    return TestClient(app), calls


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for child in value.values() for item in _strings(child)]
    if isinstance(value, list):
        return [item for child in value for item in _strings(child)]
    return []


def test_lab_page_offers_the_fault_and_keeps_ground_truth_on_this_service(tmp_path):
    client, _calls = _client(tmp_path, lambda _url, _body: {})
    with client:
        page = client.get("/")
        faults = client.get("/api/faults")
        script = client.get("/assets/app.js")

    assert page.status_code == 200
    assert "On-Call Copilot · Lab" in page.text
    assert "c1_single" in page.text
    assert "Incident Console" not in page.text
    assert script.status_code == 200
    assert "Arm fault" in script.text
    assert "Page the agent now" in script.text
    assert "Ground truth" in script.text

    payload = faults.json()
    assert payload["topology"] == "c1_single"
    assert payload["console_url"] == "http://localhost:8000/"
    payment = payload["faults"][0]
    assert payment["fault_id"] == "payment-failure"
    assert payment["flag"] == "paymentFailure"
    assert payment["arm_variant"] == "100%"
    assert payment["disarm_variant"] == "off"
    assert payment["armed"] is False
    assert payment["root_cause_services"] == ["payment"]
    assert payment["can_page"] is True
    assert payment["symptom"]["alertname"] == "CheckoutErrorRateHigh"
    assert payment["symptom"]["service"] == "checkout"


def test_page_posts_the_symptom_and_a_repeat_keeps_the_same_fingerprint(tmp_path):
    client, calls = _client(
        tmp_path,
        lambda _url, _body: {"incidents": [{"incident_id": "alert-1"}]},
    )
    with client:
        first = client.post("/api/faults/payment-failure/page", json={"tool_budget": 4})
        second = client.post("/api/faults/payment-failure/page", json={})

    assert first.status_code == 200
    assert second.status_code == 200
    assert [url for url, _body in calls] == [
        "http://oncall.test/alerts?tool_budget=4",
        "http://oncall.test/alerts?tool_budget=10",
    ]
    first_alert, second_alert = (body for _url, body in calls)
    assert first_alert == second_alert
    assert first_alert["alerts"][0]["fingerprint"] == "checkout-error-rate-high"
    assert first_alert["alerts"][0]["labels"] == {
        "alertname": "CheckoutErrorRateHigh",
        "service": "checkout",
        "severity": "critical",
    }
    assert first.json()["incident_id"] == "alert-1"
    leaked = [text for text in _strings(first_alert) if any(token in text for token in FORBIDDEN)]
    assert leaked == []


def test_tool_budget_outside_the_agent_limit_is_rejected(tmp_path):
    client, calls = _client(tmp_path, lambda _url, _body: {})
    with client:
        response = client.post("/api/faults/payment-failure/page", json={"tool_budget": 0})

    assert response.status_code == 422
    assert calls == []


def test_arm_changes_only_the_named_flag_and_disarm_restores_it(tmp_path):
    flag_path = tmp_path / "demo.flagd.json"
    original = json.loads(SHOP_FLAGS.read_text(encoding="utf-8"))
    flag_path.write_text(json.dumps(original), encoding="utf-8")
    armed_once = json.loads(json.dumps(original))
    armed_once["flags"]["paymentFailure"]["defaultVariant"] = "100%"
    client, calls = _client(tmp_path, lambda _url, _body: {}, flag_path=flag_path)
    with client:
        armed = client.post("/api/faults/payment-failure/arm")
        during = json.loads(flag_path.read_text(encoding="utf-8"))
        unchanged = flag_path.read_bytes()
        again = client.post("/api/faults/payment-failure/arm")
        assert flag_path.read_bytes() == unchanged
        disarmed = client.post("/api/faults/payment-failure/disarm")

    assert armed.status_code == 200
    assert armed.json()["changed"] is True
    assert again.json()["changed"] is False
    assert disarmed.json()["changed"] is True
    assert calls == []
    assert during == armed_once
    assert json.loads(flag_path.read_text(encoding="utf-8")) == original


def test_arm_refuses_a_variant_the_live_file_does_not_have(tmp_path):
    flag_path = _flags(tmp_path)
    document = json.loads(flag_path.read_text(encoding="utf-8"))
    del document["flags"]["paymentFailure"]["variants"]["100%"]
    flag_path.write_text(json.dumps(document), encoding="utf-8")
    before = flag_path.read_bytes()
    client, _calls = _client(tmp_path, lambda _url, _body: {}, flag_path=flag_path)
    with client:
        response = client.post("/api/faults/payment-failure/arm")

    assert response.status_code == 409
    assert "100%" in response.json()["detail"]
    assert flag_path.read_bytes() == before


def test_unknown_fault_is_not_found(tmp_path):
    client, calls = _client(tmp_path, lambda _url, _body: {})
    with client:
        response = client.post("/api/faults/not-a-fault/arm")

    assert response.status_code == 404
    assert calls == []


def _gap_manifest(path: Path) -> None:
    path.write_text(
        """
faults:
  - fault_id: example-gap
    enable:
      action: set_flag
      args:
        flag: adFailure
        variant: "on"
    disable:
      action: set_flag
      args:
        flag: adFailure
        variant: "off"
    root_cause_services:
      - ad
    complexity: single
    correct_action:
      action: set_flag
      args:
        flag: adFailure
        variant: "off"
    detect:
      query: up
      operator: ">"
      threshold: 0
    recovery:
      query: up
      operator: ">"
      threshold: 0
""".strip(),
        encoding="utf-8",
    )


def test_a_published_symptom_must_name_a_manifest_fault(tmp_path):
    manifest = tmp_path / "manifest.yaml"
    _gap_manifest(manifest)
    with pytest.raises(CatalogError, match="payment-failure"):
        load_catalog(manifest)


def test_a_fault_without_a_published_symptom_cannot_page(tmp_path):
    manifest = tmp_path / "manifest.yaml"
    _gap_manifest(manifest)
    client, calls = _client(tmp_path, lambda _url, _body: {}, manifest=manifest, symptoms={})
    with client:
        faults = client.get("/api/faults")
        armed = client.post("/api/faults/example-gap/arm")
        paged = client.post("/api/faults/example-gap/page", json={})

    assert faults.json()["faults"][0]["can_page"] is False
    assert faults.json()["faults"][0]["root_cause_services"] == ["ad"]
    assert armed.status_code == 200
    assert armed.json()["fault"]["current_variant"] == "on"
    assert paged.status_code == 422
    assert calls == []


def test_notification_uses_only_the_symptom_fields():
    symptom = load_catalog(MANIFEST)[0].symptom
    assert symptom is not None
    body = notification(symptom, "2026-11-03T12:00:00Z")
    alert = body["alerts"][0]
    assert set(alert) == {"status", "fingerprint", "startsAt", "labels", "annotations"}
    assert set(alert["labels"]) == {"alertname", "service", "severity"}
    assert set(alert["annotations"]) == {"summary"}
    assert symptom.grafana_note not in _strings(body)
