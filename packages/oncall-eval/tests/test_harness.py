import json
from pathlib import Path

import pytest
from oncall_eval.harness import main

ROOT = Path(__file__).resolve().parents[3]


def test_mock_cli_replays_development_fixture_without_live_dependencies(capsys):
    result = main(
        [
            "--mock",
            "--cases",
            str(ROOT / "eval" / "dev"),
            "--fixtures",
            str(ROOT / "eval" / "fixtures"),
            "--manifest",
            str(ROOT / "eval" / "faults" / "manifest.yaml"),
            "--group-by",
            "complexity",
            "--fail-under",
            "1.0",
        ]
    )

    assert result == 0
    output = capsys.readouterr().out
    assert "no model or live fault injection" in output
    assert "dev-001: PASS" in output
    assert "single: 1/1" in output


def test_mock_cli_fails_when_a_valid_case_has_no_fixture(tmp_path: Path, capsys):
    cases = tmp_path / "cases"
    cases.mkdir()
    source = ROOT / "eval" / "dev" / "001-payment-failure.json"
    cases.joinpath(source.name).write_bytes(source.read_bytes())

    result = main(
        [
            "--mock",
            "--cases",
            str(cases),
            "--fixtures",
            str(tmp_path / "empty"),
            "--manifest",
            str(ROOT / "eval" / "faults" / "manifest.yaml"),
            "--fail-under",
            "0.0",
        ]
    )

    assert result == 1
    assert "no fixture" in capsys.readouterr().out


def test_mock_cli_fails_on_run_record_contract_drift(tmp_path: Path, capsys):
    cases = tmp_path / "cases"
    fixtures = tmp_path / "fixtures"
    cases.mkdir()
    fixtures.mkdir()
    source = ROOT / "eval" / "dev" / "001-payment-failure.json"
    cases.joinpath(source.name).write_bytes(source.read_bytes())
    fixture = json.loads((ROOT / "eval" / "fixtures" / "dev-001.runrecord.json").read_text())
    fixture.pop("tokens")
    fixtures.joinpath("dev-001.runrecord.json").write_text(json.dumps(fixture))

    result = main(
        [
            "--mock",
            "--cases",
            str(cases),
            "--fixtures",
            str(fixtures),
            "--manifest",
            str(ROOT / "eval" / "faults" / "manifest.yaml"),
            "--fail-under",
            "0.0",
        ]
    )

    assert result == 1
    assert "tokens" in capsys.readouterr().out


def test_live_cli_requires_explicit_fault_injection_acknowledgement(capsys):
    result = main(
        [
            "--live",
            "--cases",
            str(ROOT / "eval" / "dev"),
            "--fail-under",
            "0.6",
        ]
    )

    assert result == 2
    assert "--yes-inject-faults" in capsys.readouterr().out


def test_cli_requires_explicit_acknowledgement_for_heldout_path():
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "--mock",
                "--cases",
                str(ROOT / "eval" / "heldout"),
                "--fail-under",
                "0.6",
            ]
        )

    assert caught.value.code == 2
