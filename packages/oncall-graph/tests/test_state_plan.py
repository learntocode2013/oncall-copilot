"""Visible plan, validate_plan, and the monitor edges retry / replan / stop."""

import pytest
from oncall_graph.planning.monitor import build_monitor_graph, monitor
from oncall_graph.planning.plan import (
    format_plan,
    validate_action,
    validate_hypotheses,
    validate_plan,
)
from oncall_graph.state import new_incident

PAYMENT_ACTION = {
    "action": "set_flag",
    "args": {"flag": "paymentFailure", "enabled": False},
    "expected_effect": "checkout error rate falls below 5%",
    "rollback": "set_flag paymentFailure enabled=true",
}

CHECKOUT_HYPOTHESIS = {
    "service": "checkout",
    "statement": "checkout is the root cause",
    "evidence_refs": ["error-rate-5m"],
    "confidence": 0.9,
}

PAYMENT_HYPOTHESIS = {
    "service": "payment",
    "statement": "paymentFailure flag",
    "evidence_refs": ["payment"],
    "confidence": 0.4,
}


def texts(state):
    return [item["text"] for item in state.get("transcript") or []]


def merge(state, update):
    """Apply one node update the way the reducers will."""
    merged = dict(state)
    for key, value in update.items():
        if key in ("transcript", "meter_rows", "diagnosis_reports", "hypotheses", "evidence"):
            merged[key] = list(state.get(key) or []) + list(value or [])
        else:
            merged[key] = value
    return merged


def test_validate_plan_accepts_one_typed_action():
    plan = validate_plan(
        {"stage": "act", "steps": [{"tool": "traces", "args": {"service": "payment"}}]}
    )
    hypotheses = validate_hypotheses([CHECKOUT_HYPOTHESIS, PAYMENT_HYPOTHESIS])
    action = validate_action(PAYMENT_ACTION)
    assert plan == {
        "stage": "act",
        "steps": [{"tool": "traces", "args": {"service": "payment"}}],
    }
    assert action["action"] == "set_flag"
    assert action["args"] == {"flag": "paymentFailure", "enabled": False}
    assert action["expected_effect"].startswith("checkout error rate")
    assert action["rollback"] == "set_flag paymentFailure enabled=true"
    assert hypotheses[0]["evidence_refs"] == ["error-rate-5m"]
    visible = format_plan(plan, hypotheses, action)
    assert "[act]" in visible
    assert "paymentFailure" in visible
    assert "rollback:" in visible


def test_validate_plan_rejects_unknown_shell_and_a_second_action():
    with pytest.raises(ValueError, match="unknown tool"):
        validate_plan({"stage": "test", "steps": [{"tool": "bash", "args": {}}]})

    with pytest.raises(ValueError, match="shell-shaped command"):
        validate_action(
            {
                "action": "restart",
                "args": {"service": "checkout; docker exec shop reboot"},
                "expected_effect": "process restarts",
                "rollback": "roll the deploy back",
            }
        )

    with pytest.raises(ValueError, match="shell-shaped command"):
        validate_action(
            {
                **PAYMENT_ACTION,
                "args": {"flag": "paymentFailure", "enabled": False, "command": "true"},
            }
        )

    with pytest.raises(ValueError, match="belong beside the plan"):
        validate_plan({"stage": "act", "proposed_action": PAYMENT_ACTION})

    with pytest.raises(ValueError, match="missing expected effect"):
        validate_action({**PAYMENT_ACTION, "expected_effect": "  "})


def test_validate_plan_rejects_mistyped_args_and_keeps_refs_not_log_lines():
    with pytest.raises(ValueError, match="boolean"):
        validate_action({**PAYMENT_ACTION, "args": {"flag": "paymentFailure", "enabled": "false"}})

    hypotheses = validate_hypotheses(
        [
            {
                "service": "checkout",
                "statement": "the log quoted an instruction",
                "evidence_refs": ["checkout-log"],
                "confidence": 0.4,
            }
        ]
    )
    assert hypotheses[0]["evidence_refs"] == ["checkout-log"]
    assert "docker exec" not in hypotheses[0]["statement"]
    plan = validate_plan({"stage": "hypotheses"})
    assert plan == {"stage": "hypotheses", "steps": []}
    assert "proposed_action" not in plan


def test_a_write_tool_hiding_in_steps_is_rejected():
    with pytest.raises(ValueError, match="writes do not belong"):
        validate_plan(
            {
                "stage": "act",
                "steps": [
                    {"tool": "promql", "args": {"query": "error_rate"}},
                    {
                        "tool": "restart",
                        "args": {"service": "payment"},
                        "expected_effect": "payment error rate drops",
                    },
                ],
            }
        )
    action = validate_action(
        {
            "action": "restart",
            "args": {"service": "payment"},
            "expected_effect": "payment error rate drops",
            "rollback": "rollback payment",
        }
    )
    assert action["action"] == "restart"
    assert action["rollback"] == "rollback payment"


def test_timeout_retries_once_then_replans():
    state = new_incident(
        alert="checkout error rate above 5% for five minutes",
        plan={"stage": "test", "steps": [{"tool": "traces", "args": {"service": "checkout"}}]},
        hypotheses=[CHECKOUT_HYPOTHESIS],
        ok=False,
        tool_calls=2,
        tool_budget=10,
        last={
            "step": {"tool": "promql", "args": {"query": "error_rate"}},
            "result": {"error": "TimeoutError: promql timed out"},
        },
    )
    first = monitor(state)
    assert first["route"] == "retry"
    assert first["why"] == "tool timeout"
    assert first["retries"] == 1
    assert texts(first) == ["monitor -> retry"]

    second = monitor(merge(state, first))
    assert second["route"] == "replan"
    assert second["why"] == "tool timeout after retry"

    graph = build_monitor_graph()
    out = graph.invoke(state)
    assert out["route"] == "retry"
    assert out["status"] == "RETRY"
    assert out["plan"]["steps"][0]["tool"] == "promql"
    assert "monitor -> retry" in texts(out)
    assert "retry" in texts(out)


def test_wrong_first_hypothesis_takes_the_replan_edge():
    """Signature A: checkout was blamed; its own report says the cause is not local."""
    state = new_incident(
        alert="checkout error rate above 5% for five minutes",
        plan={"stage": "test"},
        hypotheses=[CHECKOUT_HYPOTHESIS, PAYMENT_HYPOTHESIS],
        tool_calls=4,
        tool_budget=10,
        diagnosis_reports=[
            {"service": "payment", "local_cause": True, "tool_calls": 1},
            {"service": "checkout", "local_cause": False, "tool_calls": 1},
        ],
    )
    graph = build_monitor_graph()
    out = graph.invoke(state)
    assert out["route"] == "replan"
    assert "monitor -> replan" in texts(out)
    assert "replan" in texts(out)
    assert out["revisions"] == 1
    assert out["stage"] == "hypotheses"
    assert out["plan"]["stage"] == "hypotheses"
    top = max(out["hypotheses"], key=lambda item: item["confidence"])
    assert top["service"] == "checkout"
    assert "contradicted" not in top
    visible = format_plan(out["plan"], out["hypotheses"], out.get("proposed_action"))
    assert "[hypotheses]" in visible


def test_failed_verification_rolls_back_in_code_then_replans():
    state = new_incident(
        plan={"stage": "verify"},
        hypotheses=[PAYMENT_HYPOTHESIS],
        proposed_action=PAYMENT_ACTION,
        verification={
            "recovered": False,
            "signal": "checkout_error_rate",
            "evidence_refs": ["checkout_error_rate"],
        },
    )
    graph = build_monitor_graph()
    out = graph.invoke(state)
    assert out["route"] == "replan"
    assert texts(out)[0] == "rollback"
    assert "monitor -> replan" in texts(out)
    assert out["pending_rollback"] == {
        "tool": "set_flag",
        "args": {"flag": "paymentFailure", "enabled": True},
    }
    assert out["proposed_action"] is None
    assert out["revisions"] == 1


def test_revision_cap_and_tool_budget_stop():
    contradicted = new_incident(
        plan={"stage": "test"},
        hypotheses=[CHECKOUT_HYPOTHESIS],
        diagnosis_reports=[{"service": "checkout", "local_cause": False, "tool_calls": 1}],
        revisions=3,
        max_revisions=3,
        tool_calls=1,
        tool_budget=10,
    )
    capped = monitor(contradicted)
    assert capped["route"] == "stop"
    assert capped["status"] == "ESCALATED"
    assert capped["why"].startswith("revision cap:")

    timed_out = new_incident(
        plan={"stage": "test"},
        hypotheses=[CHECKOUT_HYPOTHESIS],
        ok=False,
        tool_calls=10,
        tool_budget=10,
        last={
            "step": {"tool": "promql", "args": {}},
            "result": {"error": "TimeoutError: timed out"},
        },
    )
    stopped = monitor(timed_out)
    assert stopped["route"] == "stop"
    assert "tool budget exhausted" in stopped["why"]

    graph = build_monitor_graph()
    out = graph.invoke(contradicted)
    assert out["status"] == "ESCALATED"
    assert texts(out)[-1] == "stop"
    assert "monitor -> stop" in texts(out)
    drawing = graph.get_graph().draw_mermaid()
    for name in ("monitor", "retry", "replan", "stop", "ready"):
        assert name in drawing


def test_verification_failure_at_the_budget_cap_stops_after_rollback():
    state = new_incident(
        plan={"stage": "verify"},
        hypotheses=[PAYMENT_HYPOTHESIS],
        proposed_action=PAYMENT_ACTION,
        verification={
            "recovered": False,
            "signal": "checkout_error_rate",
            "evidence_refs": ["checkout_error_rate"],
        },
        tool_calls=10,
        tool_budget=10,
    )
    update = monitor(state)
    assert update["route"] == "stop"
    assert update["status"] == "ESCALATED"
    assert texts(update)[0] == "rollback"
    assert "monitor -> stop" in texts(update)
    assert update["pending_rollback"]["args"]["enabled"] is True


def test_ready_when_the_evidence_supports_the_hypothesis():
    state = new_incident(
        plan={"stage": "test"},
        hypotheses=[PAYMENT_HYPOTHESIS],
        diagnosis_reports=[{"service": "payment", "local_cause": True, "tool_calls": 1}],
        tool_calls=10,
        tool_budget=10,
    )
    out = build_monitor_graph().invoke(state)
    assert out["route"] == "ready"
    assert out["status"] == "READY"
    assert "monitor -> ready" in texts(out)
    assert "contradicted" not in out["hypotheses"][0]


def test_graph_channel_uses_the_contract_fields_and_keeps_its_own_name():
    """The channel carries the contract fields. It is not the contract type."""
    from oncall_contracts.state import Hypothesis
    from oncall_contracts.state import IncidentState as ContractState
    from oncall_graph.state import IncidentChannel

    assert IncidentChannel is not ContractState
    for field in (
        "alert",
        "scope",
        "severity",
        "hypotheses",
        "evidence",
        "plan",
        "verification",
        "transcript",
        "proposed_action",
    ):
        assert field in IncidentChannel.__annotations__
        assert field in ContractState.__annotations__
    for field in (
        "incident_id",
        "stage",
        "approval_payload_hash",
        "last",
        "ok",
        "why",
        "route",
        "retries",
        "revisions",
        "max_revisions",
        "tool_budget",
        "diagnosis_reports",
        "diagnosis_calls_accounted",
        "reinvestigate",
        "pending_rollback",
        "status",
        "meter_rows",
        "tool_calls",
    ):
        assert field in IncidentChannel.__annotations__
        assert field not in ContractState.__annotations__
    assert "evidence_refs" in Hypothesis.__annotations__
    assert "confidence" in Hypothesis.__annotations__
