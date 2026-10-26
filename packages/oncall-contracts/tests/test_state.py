from typing import Annotated, get_args, get_origin, get_type_hints

from oncall_contracts.state import Hypothesis, IncidentState


def test_incident_state_has_the_fields_the_graph_needs():
    hints = IncidentState.__annotations__
    for field in (
        "alert",
        "scope",
        "hypotheses",
        "evidence",
        "plan",
        "proposed_action",
        "verification",
        "transcript",
    ):
        assert field in hints, f"IncidentState missing {field!r}"


def test_hypotheses_channel_accumulates():
    """Parallel diagnosis agents each append; the channel must carry a reducer."""
    ann = IncidentState.__annotations__["hypotheses"]
    assert get_origin(ann) is Annotated, "hypotheses must be Annotated with a reducer"
    reducer = get_args(ann)[1]
    assert reducer([{"service": "a"}], [{"service": "b"}]) == [{"service": "a"}, {"service": "b"}]


def test_hypothesis_carries_evidence_and_confidence():
    hints = get_type_hints(Hypothesis)
    for field in ("service", "statement", "evidence_refs", "confidence"):
        assert field in hints, f"Hypothesis missing {field!r}"
