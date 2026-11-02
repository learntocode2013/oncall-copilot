import json
from pathlib import Path

from oncall_eval.cases import load_case, validate_case
from oncall_eval.manifest import load_manifest

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "eval" / "faults" / "manifest.yaml"
FLAGD = ROOT / "deploy" / "flagd" / "demo.flagd.json"


def test_manifest_parses_and_fault_ids_are_unique():
    faults = load_manifest(MANIFEST)
    assert list(faults) == ["payment-failure"]
    assert faults["payment-failure"]["detect"]["threshold"] == 0.5
    assert faults["payment-failure"]["recovery"]["operator"] == ">"


def test_manifest_actions_exist_in_the_real_flag_config():
    flags = json.loads(FLAGD.read_text(encoding="utf-8"))["flags"]
    for fault in load_manifest(MANIFEST).values():
        for field in ("enable", "disable"):
            action = fault[field]
            assert action["action"] == "set_flag"
            flag = action["args"]["flag"]
            variant = action["args"]["variant"]
            assert flag in flags
            assert variant in flags[flag]["variants"]


def test_development_cases_match_manifest_ground_truth():
    faults = load_manifest(MANIFEST)
    paths = sorted((ROOT / "eval" / "dev").glob("*.json"))
    assert paths, "at least one development case is required"
    for path in paths:
        case = load_case(path)
        assert not validate_case(case, faults), path.name


def test_manifest_fault_has_a_cause_runbook():
    runbook_ids = set()
    for path in (ROOT / "data" / "runbooks").glob("*.md"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("fault_id:"):
                runbook_ids.add(line.partition(":")[2].strip())
                break
    assert set(load_manifest(MANIFEST)) <= runbook_ids
