"""The lab is its own Compose service and is part of make up."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_compose_publishes_the_lab_beside_the_incident_api():
    compose = (ROOT / "deploy/compose.oncall.yaml").read_text(encoding="utf-8")
    makefile = (ROOT / "deploy/Makefile").read_text(encoding="utf-8")

    assert "oncall-lab:" in compose
    assert '"8020:8020"' in compose
    assert "ONCALL_ALERTS_URL: http://oncall-service:8000/alerts" in compose
    assert "/eval/faults/manifest.yaml:/lab/manifest.yaml:ro" in compose
    assert "/deploy/flagd:/etc/flagd" in compose
    assert "build oncall-service oncall-lab" in makefile
    assert "localhost:8020/healthz" in makefile
    assert "http://localhost:8020/" in makefile
