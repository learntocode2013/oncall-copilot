from copy import deepcopy

from oncall_contracts.faults import FaultAction
from oncall_eval.cases import EvalCase
from oncall_eval.scoring import SystemRun, score_run


def _case(*, roots=None, action: FaultAction | None = None, budget: int = 5) -> EvalCase:
    expected_action = action or {
        "action": "set_flag",
        "args": {"flag": "paymentFailure", "variant": "off"},
    }
    return {
        "case_id": "dev-001",
        "fault_id": "payment-failure",
        "complexity": "single" if len(roots or ["payment"]) == 1 else "multi",
        "variant": "100%",
        "alert": {
            "status": "firing",
            "labels": {"alertname": "CheckoutErrorRateHigh", "service": "checkout"},
            "annotations": {"summary": "Checkout failures"},
            "startsAt": "2026-11-02T00:00:00Z",
        },
        "expected": {
            "root_cause_services": roots or ["payment"],
            "correct_action": expected_action,
            "max_tool_calls": budget,
            "harmful_actions": 0,
        },
    }


def _run(*, roots=None, action: FaultAction | None = None) -> SystemRun:
    proposed = action or {
        "action": "set_flag",
        "args": {"flag": "paymentFailure", "variant": "off"},
    }
    return {
        "root_cause_services": roots or ["payment"],
        "proposed_action": proposed,
        "executed_actions": [proposed],
        "tool_calls": 4,
        "tokens": 100,
        "usd": 0.01,
        "verified_recovery": True,
        "rolled_back": False,
        "model_ids": {"solo": "test-model"},
    }


def test_score_run_passes_exact_code_first_outcome():
    score = score_run(_case(), _run())

    assert score.passed
    assert score.correct_root_cause
    assert score.root_cause_rank == 1
    assert score.correct_action_proposed
    assert score.harmful_actions == 0
    assert score.within_tool_budget
    assert score.reasons == ()


def test_score_run_requires_all_multi_service_roots_near_the_top():
    score = score_run(
        _case(roots=["payment", "cart"]),
        _run(roots=["payment", "frontend", "cart"]),
    )

    assert not score.correct_root_cause
    assert score.root_cause_rank == 3
    assert not score.passed


def test_score_run_counts_wrong_and_duplicate_writes_as_harmful():
    run = _run()
    correct = run["executed_actions"][0]
    run["executed_actions"] = [
        correct,
        deepcopy(correct),
        {"action": "restart", "args": {"service": "payment"}},
    ]

    score = score_run(_case(), run)

    assert score.harmful_actions == 2
    assert not score.passed
    assert "harmful actions" in score.reasons[0]


def test_score_run_reports_action_recovery_budget_and_system_failures():
    run = _run(action={"action": "restart", "args": {"service": "payment"}})
    run["tool_calls"] = 6
    run["verified_recovery"] = False
    run["error"] = "model unavailable"

    score = score_run(_case(budget=5), run)

    assert not score.correct_action_proposed
    assert not score.within_tool_budget
    assert score.reasons == (
        "incorrect action proposal",
        "recovery not verified by the system",
        "harmful actions: expected 0, observed 1",
        "tool budget exceeded: 6 > 5",
        "system error: model unavailable",
    )
