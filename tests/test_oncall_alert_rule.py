"""The checkout alert Grafana posts is the same detector as the fault manifest."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RULE = ROOT / "deploy/grafana/provisioning/alerting/oncall-copilot.yaml"
COMPOSE = ROOT / "deploy/compose.oncall.yaml"


def _rule_text() -> str:
    return RULE.read_text(encoding="utf-8")


def test_checkout_rule_uses_the_manifest_detector():
    manifest = yaml.safe_load((ROOT / "eval/faults/manifest.yaml").read_text(encoding="utf-8"))
    payment = next(fault for fault in manifest["faults"] if fault["fault_id"] == "payment-failure")
    document = yaml.safe_load(_rule_text())
    expression = document["groups"][0]["rules"][0]["data"][0]["model"]["expr"]

    def normalized(value: str) -> str:
        return "".join(value.split())

    assert normalized(payment["detect"]["query"]) in normalized(expression)
    assert "> 0.05" in expression
    assert "http_route" not in expression


def test_rule_posts_to_the_incident_api_on_the_compose_network():
    document = yaml.safe_load(_rule_text())
    receiver = document["contactPoints"][0]["receivers"][0]
    rule = document["groups"][0]["rules"][0]
    compose = COMPOSE.read_text(encoding="utf-8")
    script = (ROOT / "deploy/grafana/start.sh").read_text(encoding="utf-8")

    assert receiver["settings"]["url"] == "http://oncall-service:8000/alerts"
    assert receiver["settings"]["httpMethod"] == "POST"
    assert rule["notification_settings"]["receiver"] == document["contactPoints"][0]["name"]
    assert rule["labels"]["service"] == "checkout"
    assert rule["for"] == "5m"
    assert rule["noDataState"] == "OK"
    assert "/etc/grafana/provisioning/alerting/oncall-copilot.yaml" not in compose
    assert "/oncall/oncall-copilot.yaml:ro" in compose
    assert "GF_PATHS_PROVISIONING=/tmp/grafana-provisioning" in compose
    assert (
        "cp /oncall/oncall-copilot.yaml /tmp/grafana-provisioning/alerting/oncall-copilot.yaml"
        in script
    )
    assert "exec /run.sh" in script
