"""The lab is its own Compose service and is part of make up."""

import subprocess
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
    # Was `assert "build oncall-service oncall-lab" in makefile`. `up` no longer
    # names the images it builds: it derives them from the services this overlay
    # defines, because the hardcoded pair was written before the three mcp-*
    # servers existed and never extended, so those ran from stale images (PR
    # #27). The intent of this line is unchanged -- the lab is part of `make
    # up` -- but it is now checked against what `up` will actually build.
    built = subprocess.run(
        ["make", "-C", str(ROOT / "deploy"), "print-our-images"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert "oncall-lab" in built, f"make up would not build the lab; it builds {built}"
    assert "oncall-service" in built
    assert "localhost:8020/healthz" in makefile
    assert "http://localhost:8020/" in makefile
