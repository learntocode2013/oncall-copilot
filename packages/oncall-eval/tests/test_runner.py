import json
from pathlib import Path

from oncall_contracts.config import Topology
from oncall_contracts.faults import FaultSpec
from oncall_contracts.state import Alert
from oncall_eval.cases import EvalCase
from oncall_eval.injector import FlagdFaultInjector
from oncall_eval.runner import EvaluationRunner, load_run_record, validate_run_record
from oncall_eval.scoring import SystemRun
from oncall_eval.signals import Observation


def _case() -> EvalCase:
    return {
        "case_id": "dev-001",
        "fault_id": "payment-failure",
        "complexity": "single",
        "variant": "100%",
        "alert": {
            "status": "firing",
            "labels": {"alertname": "CheckoutErrorRateHigh", "service": "checkout"},
            "annotations": {"summary": "Checkout failures"},
            "startsAt": "2026-11-02T00:00:00Z",
        },
        "expected": {
            "root_cause_services": ["payment"],
            "correct_action": {
                "action": "set_flag",
                "args": {"flag": "paymentFailure", "variant": "off"},
            },
            "max_tool_calls": 5,
            "harmful_actions": 0,
        },
    }


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


def _injector(tmp_path: Path) -> FlagdFaultInjector:
    path = tmp_path / "flagd.json"
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
    return FlagdFaultInjector(path)


class ObservingPoller:
    def __init__(self, injector: FlagdFaultInjector):
        self.injector = injector

    def wait(self, _signal, *, phase: str) -> Observation:
        expected = "100%" if phase == "detection" else "off"
        assert self.injector.current_variant("paymentFailure") == expected
        return Observation(phase=phase, value=1.0, attempts=1, elapsed_seconds=0.0)


class PassingSystem:
    def __init__(self):
        self.received = None

    def run(
        self,
        alert: Alert,
        *,
        incident_id: str,
        config_id: Topology,
        tool_budget: int,
    ) -> SystemRun:
        self.received = (alert, incident_id, config_id, tool_budget)
        action = {
            "action": "set_flag",
            "args": {"flag": "paymentFailure", "variant": "off"},
        }
        return {
            "root_cause_services": ["payment"],
            "proposed_action": action,
            "executed_actions": [action],
            "tool_calls": 4,
            "tokens": 8120,
            "usd": 0.0031,
            "verified_recovery": True,
            "rolled_back": False,
            "model_ids": {"solo": "test-model"},
            "playbook_hash": None,
        }


class FailingSystem:
    def run(self, _alert: Alert, **_kwargs) -> SystemRun:
        raise RuntimeError("model unavailable")


def test_runner_executes_scores_and_persists_one_record(tmp_path: Path):
    injector = _injector(tmp_path)
    system = PassingSystem()
    times = iter([10.0, 10.25])
    runner = EvaluationRunner(
        injector=injector,
        poller=ObservingPoller(injector),  # type: ignore[arg-type]
        system=system,
        artifacts_dir=tmp_path / "runs",
        clock=lambda: next(times),
        id_factory=lambda: "run-001",
    )

    result = runner.run(_case(), _fault(), "c1_single")

    assert result.score.passed
    assert result.record["latency_ms"] == 250
    assert result.record["correct_root_cause"]
    assert result.record["correct_action_proposed"]
    assert result.record["verified_recovery"]
    assert result.artifact_path == tmp_path / "runs" / "run-001.json"
    assert load_run_record(result.artifact_path) == result.record
    assert system.received == (_case()["alert"], "eval-run-001", "c1_single", 5)
    assert injector.current_variant("paymentFailure") == "off"


def test_runner_persists_failure_and_still_cleans_up_fault(tmp_path: Path):
    injector = _injector(tmp_path)
    times = iter([4.0, 4.125])
    runner = EvaluationRunner(
        injector=injector,
        poller=ObservingPoller(injector),  # type: ignore[arg-type]
        system=FailingSystem(),
        artifacts_dir=tmp_path / "runs",
        clock=lambda: next(times),
        id_factory=lambda: "run-failed",
    )

    result = runner.run(_case(), _fault(), "c1_single")

    assert not result.score.passed
    assert result.record["error"] == "RuntimeError: model unavailable"
    assert result.record["latency_ms"] == 125
    assert result.record["tool_calls"] == 0
    assert result.artifact_path.is_file()
    assert injector.current_variant("paymentFailure") == "off"


def test_run_record_validator_rejects_contract_drift(tmp_path: Path):
    injector = _injector(tmp_path)
    system = PassingSystem()
    runner = EvaluationRunner(
        injector=injector,
        poller=ObservingPoller(injector),  # type: ignore[arg-type]
        system=system,
        artifacts_dir=tmp_path / "runs",
        id_factory=lambda: "valid",
    )
    record = dict(runner.run(_case(), _fault(), "c1_single").record)
    record.pop("tokens")
    record["surprise"] = True

    problems = validate_run_record(record)

    assert any("missing fields ['tokens']" in problem for problem in problems)
    assert any("unknown fields ['surprise']" in problem for problem in problems)
