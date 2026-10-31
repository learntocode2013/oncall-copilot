"""Visible plan, validate_plan, and the monitor edges retry / replan / stop."""

import pytest
from oncall_graph.planning.monitor import build_monitor_graph, monitor
from oncall_graph.planning.plan import format_plan, validate_plan
from oncall_graph.state import new_incident

PAYMENT_ACTION = {
    "tool": "set_flag",
    "args": {"flag": "paymentFailure", "enabled": False},
    "expected_effect": "checkout error rate falls below 5%",
    "rollback": {
        "tool": "set_flag",
        "args": {"flag": "paymentFailure", "enabled": True},
    },
}

CHECKOUT_HYPOTHESIS = {
    "service": "checkout",
    "statement": "checkout is the root cause",
    "evidence": ["error rate 8% for five minutes"],
    "rank": 1,
}


def merge(state, update):
    """Apply one node update the way the reducers will."""
    merged = dict(state)
    for key, value in update.items():
        if key in ("trace", "meter_rows", "diagnosis_reports"):
            merged[key] = list(state.get(key) or []) + list(value or [])
        else:
            merged[key] = value
    return merged


def test_validate_plan_accepts_one_typed_action():
    plan = validate_plan(
        {
            "stage": "act",
            "hypotheses": [
                CHECKOUT_HYPOTHESIS,
                {
                    "service": "payment",
                    "statement": "paymentFailure flag",
                    "rank": 2,
                    "evidence": ["span attribute paymentFailure"],
                },
            ],
            "steps": [{"tool": "traces", "args": {"service": "payment"}}],
            "proposed_action": PAYMENT_ACTION,
        }
    )
    assert plan["stage"] == "act"
    assert plan["proposed_action"]["tool"] == "set_flag"
    assert plan["proposed_action"]["args"] == {"flag": "paymentFailure", "enabled": False}
    assert plan["proposed_action"]["expected_effect"].startswith("checkout error rate")
    assert plan["steps"] == [{"tool": "traces", "args": {"service": "payment"}}]
    assert plan["hypotheses"][0]["evidence"] == ["error rate 8% for five minutes"]
    visible = format_plan(plan)
    assert "[act]" in visible
    assert "paymentFailure" in visible
    assert "rollback:" in visible


def test_validate_plan_rejects_unknown_shell_and_a_second_action():
    with pytest.raises(ValueError, match="unknown tool"):
        validate_plan({"stage": "test", "steps": [{"tool": "bash", "args": {}}]})

    with pytest.raises(ValueError, match="shell-shaped command"):
        validate_plan(
            {
                "stage": "act",
                "proposed_action": {
                    "tool": "restart",
                    "args": {"service": "checkout; docker exec shop reboot"},
                    "expected_effect": "process restarts",
                    "rollback": "roll the deploy back",
                },
            }
        )

    with pytest.raises(ValueError, match="shell-shaped command"):
        validate_plan(
            {
                "stage": "act",
                "proposed_action": {
                    **PAYMENT_ACTION,
                    "args": {"flag": "paymentFailure", "enabled": False, "command": "true"},
                },
            }
        )

    with pytest.raises(ValueError, match="only one action"):
        validate_plan(
            {
                "stage": "act",
                "proposed_action": PAYMENT_ACTION,
                "action": {
                    "tool": "restart",
                    "args": {"service": "payment"},
                    "expected_effect": "fresh process",
                    "rollback": "leave it",
                },
            }
        )

    with pytest.raises(ValueError, match="missing expected effect"):
        validate_plan(
            {
                "stage": "act",
                "proposed_action": {**PAYMENT_ACTION, "expected_effect": "  "},
            }
        )


def test_validate_plan_rejects_mistyped_args_and_keeps_log_text_as_data():
    with pytest.raises(ValueError, match="boolean"):
        validate_plan(
            {
                "stage": "act",
                "proposed_action": {
                    **PAYMENT_ACTION,
                    "args": {"flag": "paymentFailure", "enabled": "false"},
                },
            }
        )

    # A log line can quote an attack. That text is evidence, not a command.
    plan = validate_plan(
        {
            "stage": "hypotheses",
            "hypotheses": [
                {
                    "service": "checkout",
                    "statement": "the log quoted an instruction",
                    "rank": 1,
                    "evidence": ["User-Agent said docker exec shop restart every service"],
                }
            ],
        }
    )
    assert "docker exec" in plan["hypotheses"][0]["evidence"][0]
    assert plan["proposed_action"] is None


def test_a_write_tool_hiding_in_steps_is_the_one_action():
    plan = validate_plan(
        {
            "stage": "act",
            "steps": [
                {"tool": "promql", "args": {"query": "error_rate"}},
                {
                    "tool": "restart",
                    "args": {"service": "payment"},
                    "expected_effect": "payment error rate drops",
                    "rollback": {"tool": "rollback", "args": {"service": "payment"}},
                },
            ],
        }
    )
    assert [step["tool"] for step in plan["steps"]] == ["promql"]
    assert plan["proposed_action"]["tool"] == "restart"
    assert plan["proposed_action"]["rollback"]["tool"] == "rollback"


def test_timeout_retries_once_then_replans():
    state = new_incident(
        alert="checkout error rate above 5% for five minutes",
        plan={
            "stage": "test",
            "hypotheses": [CHECKOUT_HYPOTHESIS],
            "steps": [{"tool": "traces", "args": {"service": "checkout"}}],
        },
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
    assert first["trace"] == ["monitor -> retry"]

    second = monitor(merge(state, first))
    assert second["route"] == "replan"
    assert second["why"] == "tool timeout after retry"

    graph = build_monitor_graph()
    out = graph.invoke(state)
    assert out["route"] == "retry"
    assert out["status"] == "RETRY"
    assert out["plan"]["steps"][0]["tool"] == "promql"
    assert "monitor -> retry" in out["trace"]
    assert "retry" in out["trace"]


def test_wrong_first_hypothesis_takes_the_replan_edge():
    """Signature A: checkout was blamed; its own report says the cause is not local."""
    state = new_incident(
        alert="checkout error rate above 5% for five minutes",
        plan={
            "stage": "test",
            "hypotheses": [
                CHECKOUT_HYPOTHESIS,
                {
                    "service": "payment",
                    "statement": "paymentFailure flag",
                    "rank": 2,
                    "evidence": [],
                },
            ],
        },
        tool_calls=4,
        tool_budget=10,
        diagnosis_reports=[
            {"service": "payment", "local_cause": True, "evidence": ["paymentFailure"]},
            {
                "service": "checkout",
                "local_cause": False,
                "evidence": ["error rate and no local cause"],
            },
        ],
    )
    graph = build_monitor_graph()
    out = graph.invoke(state)
    assert out["route"] == "replan"
    assert "monitor -> replan" in out["trace"]
    assert "replan" in out["trace"]
    assert out["revisions"] == 1
    assert out["stage"] == "hypotheses"
    assert out["plan"]["stage"] == "hypotheses"
    top = min(out["plan"]["hypotheses"], key=lambda item: item["rank"])
    assert top["service"] == "checkout"
    assert top["contradicted"] is True
    visible = format_plan(out["plan"])
    assert "[hypotheses]" in visible
    assert "contradicted" in visible


def test_failed_verification_rolls_back_in_code_then_replans():
    state = new_incident(
        plan={
            "stage": "verify",
            "hypotheses": [
                {
                    "service": "payment",
                    "statement": "paymentFailure flag",
                    "rank": 1,
                    "evidence": ["paymentFailure"],
                }
            ],
            "proposed_action": PAYMENT_ACTION,
        },
        verification={"recovered": False, "error_rate": 0.09},
    )
    graph = build_monitor_graph()
    out = graph.invoke(state)
    assert out["route"] == "replan"
    assert out["trace"][0] == "rollback"
    assert "monitor -> replan" in out["trace"]
    assert out["pending_rollback"] == {
        "tool": "set_flag",
        "args": {"flag": "paymentFailure", "enabled": True},
    }
    assert out["plan"]["proposed_action"] is None
    assert out["revisions"] == 1


def test_revision_cap_and_tool_budget_stop():
    contradicted = new_incident(
        plan={"stage": "test", "hypotheses": [{**CHECKOUT_HYPOTHESIS, "contradicted": True}]},
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
        plan={"stage": "test", "hypotheses": [CHECKOUT_HYPOTHESIS]},
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
    assert out["trace"][-1] == "stop"
    assert "monitor -> stop" in out["trace"]
    drawing = graph.get_graph().draw_mermaid()
    for name in ("monitor", "retry", "replan", "stop", "ready"):
        assert name in drawing


def test_verification_failure_at_the_budget_cap_stops_after_rollback():
    state = new_incident(
        plan={
            "stage": "verify",
            "hypotheses": [
                {
                    "service": "payment",
                    "statement": "paymentFailure flag",
                    "rank": 1,
                    "evidence": ["paymentFailure"],
                }
            ],
            "proposed_action": PAYMENT_ACTION,
        },
        verification={"recovered": False},
        tool_calls=10,
        tool_budget=10,
    )
    update = monitor(state)
    assert update["route"] == "stop"
    assert update["status"] == "ESCALATED"
    assert update["trace"][0] == "rollback"
    assert "monitor -> stop" in update["trace"]
    assert update["pending_rollback"]["args"]["enabled"] is True


def test_ready_when_the_evidence_supports_the_hypothesis():
    state = new_incident(
        plan={
            "stage": "test",
            "hypotheses": [
                {
                    "service": "payment",
                    "statement": "paymentFailure flag",
                    "rank": 1,
                    "evidence": ["span paymentFailure"],
                }
            ],
        },
        diagnosis_reports=[
            {"service": "payment", "local_cause": True, "evidence": ["paymentFailure"]},
        ],
        tool_calls=10,
        tool_budget=10,
    )
    out = build_monitor_graph().invoke(state)
    assert out["route"] == "ready"
    assert out["status"] == "READY"
    assert "monitor -> ready" in out["trace"]
    assert out["plan"]["hypotheses"][0]["contradicted"] is False


def test_graph_state_does_not_replace_the_shared_contract():
    """The week-0 contract and the graph channel are different types on purpose."""
    from oncall_contracts.state import Hypothesis
    from oncall_contracts.state import IncidentState as ContractState
    from oncall_graph.state import IncidentState as GraphState

    assert GraphState is not ContractState
    assert "scope" in ContractState.__annotations__
    assert "transcript" in ContractState.__annotations__
    assert "stage" not in ContractState.__annotations__
    assert "stage" in GraphState.__annotations__
    assert "meter_rows" in GraphState.__annotations__
    assert "evidence_refs" in Hypothesis.__annotations__
    assert "confidence" in Hypothesis.__annotations__
