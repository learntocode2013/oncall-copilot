"""The run record IS the E1 result table.

Experiment E1 asks whether the agent justifies its cost by comparing a fixed
workflow (C0), one agent with all tools (C1) and the team (C2). Any claim of
the form "the team helps on complex incidents but adds overhead on simple
ones" is conditional, so every record must carry BOTH which configuration
produced it and which complexity stratum the case belongs to. Without those
two fields the comparison can only ever be one aggregate number.
"""

from typing import get_args

from oncall_contracts.config import Complexity, Topology
from oncall_contracts.runrecord import RunRecord


def test_all_three_experiment_configurations_are_nameable():
    assert set(get_args(Topology)) == {"c0_workflow", "c1_single", "c2_team"}


def test_complexity_strata_are_nameable():
    assert set(get_args(Complexity)) == {"single", "multi"}


def test_record_can_group_by_configuration_and_stratum():
    """Group by these two and the conditional claim either appears or it does not."""
    for field in ("config_id", "complexity"):
        assert field in RunRecord.__annotations__, f"RunRecord missing {field!r}"


def test_record_carries_the_overhead_measures():
    """\"Adds overhead\" is only measurable if cost is recorded per case."""
    for field in ("tool_calls", "tokens", "usd", "latency_ms"):
        assert field in RunRecord.__annotations__, f"RunRecord missing {field!r}"


def test_record_carries_the_outcome_measures():
    for field in (
        "correct_root_cause",
        "root_cause_rank",
        "correct_action_proposed",
        "verified_recovery",
        "harmful_actions",
        "rolled_back",
    ):
        assert field in RunRecord.__annotations__, f"RunRecord missing {field!r}"


def test_record_pins_the_playbook_it_ran_with():
    """Evidence that the playbook was frozen before the held-out run."""
    assert "playbook_hash" in RunRecord.__annotations__
