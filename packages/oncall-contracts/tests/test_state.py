from typing import Annotated, NotRequired, get_args, get_origin, get_type_hints

from oncall_contracts.config import Severity
from oncall_contracts.state import (
    Alert,
    EvidenceRef,
    Hypothesis,
    IncidentState,
    Plan,
    ProposedAction,
    Scope,
    TranscriptEntry,
    Verification,
)


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


def test_alert_is_the_validated_object():
    hints = get_type_hints(Alert)
    for field in ("status", "labels", "annotations", "startsAt"):
        assert field in hints, f"Alert missing {field!r}"
    assert get_type_hints(IncidentState)["alert"] is Alert
    assert "alertname" in get_type_hints(hints["labels"])
    assert "service" in get_type_hints(hints["labels"])
    assert "summary" in get_type_hints(hints["annotations"])


def test_scope_is_the_suspect_service_list():
    assert get_type_hints(Scope)["services"] == list[str]
    annotation = IncidentState.__annotations__["scope"]
    assert get_origin(annotation) is NotRequired
    assert get_args(annotation)[0] is Scope


def test_severity_stays_optional_until_triage():
    annotation = IncidentState.__annotations__["severity"]
    assert get_origin(annotation) is NotRequired
    assert get_args(annotation)[0] is Severity


def test_evidence_items_are_references_and_accumulate():
    hints = get_type_hints(EvidenceRef)
    assert set(hints) == {"kind", "ref", "source"}
    annotation = IncidentState.__annotations__["evidence"]
    assert get_origin(annotation) is Annotated
    assert get_args(get_args(annotation)[0])[0] is EvidenceRef
    reducer = get_args(annotation)[1]
    assert reducer([{"kind": "promql"}], [{"kind": "trace"}]) == [
        {"kind": "promql"},
        {"kind": "trace"},
    ]


def test_plan_is_the_visible_stage_and_read_steps():
    hints = get_type_hints(Plan)
    assert set(hints) == {"stage", "steps"}
    step = get_args(hints["steps"])[0]
    assert set(get_type_hints(step)) == {"tool", "args"}
    annotation = IncidentState.__annotations__["plan"]
    assert get_origin(annotation) is NotRequired
    assert get_args(annotation)[0] is Plan


def test_proposed_action_is_one_allow_listed_write():
    hints = get_type_hints(ProposedAction)
    assert set(hints) == {"action", "args", "expected_effect", "rollback"}
    assert get_args(hints["action"]) == ("set_flag", "restart", "scale", "rollback")
    assert hints["rollback"] is str
    annotation = IncidentState.__annotations__["proposed_action"]
    assert get_origin(annotation) is NotRequired
    assert get_args(annotation)[0] is ProposedAction


def test_verification_is_the_measured_result():
    hints = get_type_hints(Verification)
    assert hints["recovered"] is bool
    assert hints["signal"] is str
    assert hints["evidence_refs"] == list[str]
    annotation = IncidentState.__annotations__["verification"]
    assert get_origin(annotation) is NotRequired
    assert get_args(annotation)[0] is Verification


def test_transcript_entries_accumulate():
    hints = get_type_hints(TranscriptEntry)
    assert set(hints) == {"kind", "text"}
    annotation = IncidentState.__annotations__["transcript"]
    assert get_origin(annotation) is Annotated
    assert get_args(get_args(annotation)[0])[0] is TranscriptEntry
    reducer = get_args(annotation)[1]
    assert reducer([{"kind": "monitor"}], [{"kind": "approval"}]) == [
        {"kind": "monitor"},
        {"kind": "approval"},
    ]


def test_hypothesis_carries_evidence_and_confidence():
    hints = get_type_hints(Hypothesis)
    for field in ("service", "statement", "evidence_refs", "confidence"):
        assert field in hints, f"Hypothesis missing {field!r}"
