"""The package imports, and ``build_graph`` is still its only public name."""

import oncall_graph
import pytest
from oncall_graph import build_graph


def test_import_graph():
    assert oncall_graph.__all__ == ["build_graph"]


def test_build_graph_compiles_the_team_by_default():
    drawing = build_graph({}).get_graph().draw_mermaid()
    for name in ("commander_plan", "triage", "diagnosis", "runbook", "remediation", "comms"):
        assert name in drawing


def test_c0_workflow_is_not_implemented():
    with pytest.raises(NotImplementedError):
        build_graph({}, topology="c0_workflow")
