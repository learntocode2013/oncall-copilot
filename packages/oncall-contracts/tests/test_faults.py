from typing import get_type_hints

from oncall_contracts.faults import FaultAction, FaultSignal, FaultSpec


def test_fault_spec_carries_injection_scoring_and_cleanup_ground_truth():
    assert set(get_type_hints(FaultSpec)) == {
        "fault_id",
        "enable",
        "disable",
        "root_cause_services",
        "complexity",
        "correct_action",
        "detect",
        "recovery",
    }


def test_fault_actions_are_structured_not_command_strings():
    assert set(get_type_hints(FaultAction)) == {"action", "args"}


def test_fault_signals_carry_an_executable_success_condition():
    assert set(get_type_hints(FaultSignal)) == {"query", "operator", "threshold"}
