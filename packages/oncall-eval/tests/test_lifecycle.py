import json
from pathlib import Path

import pytest
from oncall_contracts.faults import FaultSpec
from oncall_eval.injector import FlagdFaultInjector
from oncall_eval.lifecycle import observed_fault
from oncall_eval.signals import Observation, SignalTimeout


def _fault() -> FaultSpec:
    return {
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
        "detect": {"query": "failure_ratio", "operator": ">=", "threshold": 0.5},
        "recovery": {"query": "payment_rate", "operator": ">", "threshold": 0.0},
    }


def _flagd(tmp_path: Path) -> Path:
    path = tmp_path / "demo.flagd.json"
    path.write_text(
        json.dumps(
            {
                "flags": {
                    "paymentFailure": {
                        "defaultVariant": "off",
                        "variants": {"off": 0, "100%": 1},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return path


class RecordingPoller:
    def __init__(self, injector: FlagdFaultInjector):
        self.injector = injector
        self.phases = []

    def wait(self, _signal, *, phase: str) -> Observation:
        self.phases.append((phase, self.injector.current_variant("paymentFailure")))
        return Observation(phase=phase, value=1.0, attempts=1, elapsed_seconds=0.0)


class FailingRecoveryPoller(RecordingPoller):
    def wait(self, signal, *, phase: str) -> Observation:
        if phase == "recovery":
            self.phases.append((phase, self.injector.current_variant("paymentFailure")))
            raise SignalTimeout("recovery timed out")
        return super().wait(signal, phase=phase)


def test_observed_fault_detects_after_enable_and_recovers_after_disable(tmp_path: Path):
    injector = FlagdFaultInjector(_flagd(tmp_path))
    poller = RecordingPoller(injector)

    with observed_fault(injector, poller, _fault()) as observed:  # type: ignore[arg-type]
        assert observed.detection.phase == "detection"
        assert observed.recovery is None
        assert injector.current_variant("paymentFailure") == "100%"

    assert observed.recovery is not None
    assert observed.recovery.phase == "recovery"
    assert poller.phases == [("detection", "100%"), ("recovery", "off")]


def test_observed_fault_still_proves_recovery_when_evaluation_raises(tmp_path: Path):
    injector = FlagdFaultInjector(_flagd(tmp_path))
    poller = RecordingPoller(injector)

    with (
        pytest.raises(RuntimeError, match="evaluation failed"),
        observed_fault(
            injector,
            poller,
            _fault(),  # type: ignore[arg-type]
        ),
    ):
        raise RuntimeError("evaluation failed")

    assert injector.current_variant("paymentFailure") == "off"
    assert poller.phases == [("detection", "100%"), ("recovery", "off")]


def test_observed_fault_preserves_evaluation_and_recovery_failures(tmp_path: Path):
    injector = FlagdFaultInjector(_flagd(tmp_path))
    poller = FailingRecoveryPoller(injector)

    with (
        pytest.raises(BaseExceptionGroup) as caught,
        observed_fault(
            injector,
            poller,
            _fault(),  # type: ignore[arg-type]
        ),
    ):
        raise RuntimeError("evaluation failed")

    assert [str(exc) for exc in caught.value.exceptions] == [
        "evaluation failed",
        "recovery timed out",
    ]
    assert injector.current_variant("paymentFailure") == "off"
