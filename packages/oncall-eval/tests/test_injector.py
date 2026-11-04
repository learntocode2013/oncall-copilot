import copy
import json
from pathlib import Path

import pytest
from oncall_contracts.faults import FaultSpec
from oncall_eval.injector import FaultInjectionError, FlagdFaultInjector


def _document() -> dict:
    return {
        "$schema": "https://flagd.dev/schema/v0/flags.json",
        "flags": {
            "paymentFailure": {
                "defaultVariant": "off",
                "state": "ENABLED",
                "variants": {"off": 0, "100%": 1},
            },
            "adFailure": {
                "defaultVariant": "off",
                "state": "ENABLED",
                "variants": {"off": False, "on": True},
            },
        },
    }


def _fault(**overrides) -> FaultSpec:
    value = {
        "fault_id": "payment-failure",
        "enable": {
            "action": "set_flag",
            "args": {"flag": "paymentFailure", "variant": "100%"},
        },
        "disable": {
            "action": "set_flag",
            "args": {"flag": "paymentFailure", "variant": "off"},
        },
        "root_cause_services": ["payment"],
        "complexity": "single",
        "correct_action": {
            "action": "set_flag",
            "args": {"flag": "paymentFailure", "variant": "off"},
        },
        "detect": {"query": "up", "operator": ">", "threshold": 0.0},
        "recovery": {"query": "up", "operator": ">", "threshold": 0.0},
    }
    value.update(overrides)
    return value  # type: ignore[return-value]


@pytest.fixture
def flagd(tmp_path: Path) -> Path:
    path = tmp_path / "demo.flagd.json"
    path.write_text(json.dumps(_document(), indent=4) + "\n", encoding="utf-8")
    return path


def test_injected_enables_fault_and_restores_exact_original_bytes(flagd: Path):
    original = flagd.read_bytes()
    injector = FlagdFaultInjector(flagd)

    with injector.injected(_fault()) as active:
        assert active.flag == "paymentFailure"
        assert active.enabled_variant == "100%"
        assert injector.current_variant("paymentFailure") == "100%"

    assert flagd.read_bytes() == original
    assert injector.current_variant("paymentFailure") == "off"


def test_injected_restores_fault_when_evaluation_raises(flagd: Path):
    injector = FlagdFaultInjector(flagd)

    with pytest.raises(RuntimeError, match="agent failed"), injector.injected(_fault()):
        raise RuntimeError("agent failed")

    assert injector.current_variant("paymentFailure") == "off"


def test_injected_preserves_unrelated_concurrent_change(flagd: Path):
    injector = FlagdFaultInjector(flagd)

    with injector.injected(_fault()):
        current = json.loads(flagd.read_text(encoding="utf-8"))
        current["flags"]["adFailure"]["defaultVariant"] = "on"
        flagd.write_text(json.dumps(current), encoding="utf-8")

    final = json.loads(flagd.read_text(encoding="utf-8"))
    assert final["flags"]["paymentFailure"]["defaultVariant"] == "off"
    assert final["flags"]["adFailure"]["defaultVariant"] == "on"


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ({"action": "shell", "args": {}}, "unsupported fault action"),
        (
            {
                "action": "set_flag",
                "args": {"flag": "paymentFailure", "variant": "missing"},
            },
            "unknown variant",
        ),
        (
            {
                "action": "set_flag",
                "args": {"flag": "adFailure", "variant": "off"},
            },
            "same flag",
        ),
    ],
)
def test_injected_rejects_unsafe_action_without_mutating_file(
    flagd: Path, replacement: dict, message: str
):
    original = flagd.read_bytes()
    fault = _fault()
    fault = copy.deepcopy(fault)
    fault["enable"] = replacement  # type: ignore[typeddict-item]

    with (
        pytest.raises(FaultInjectionError, match=message),
        FlagdFaultInjector(flagd).injected(fault),
    ):
        pass

    assert flagd.read_bytes() == original


def test_injected_refuses_to_overwrite_non_baseline_flag(flagd: Path):
    document = json.loads(flagd.read_text(encoding="utf-8"))
    document["flags"]["paymentFailure"]["defaultVariant"] = "100%"
    flagd.write_text(json.dumps(document), encoding="utf-8")
    before = flagd.read_bytes()

    with (
        pytest.raises(FaultInjectionError, match="expected baseline"),
        FlagdFaultInjector(flagd).injected(_fault()),
    ):
        pass

    assert flagd.read_bytes() == before


def test_injected_preserves_file_permissions(flagd: Path):
    flagd.chmod(0o640)

    with FlagdFaultInjector(flagd).injected(_fault()):
        assert flagd.stat().st_mode & 0o777 == 0o640

    assert flagd.stat().st_mode & 0o777 == 0o640
