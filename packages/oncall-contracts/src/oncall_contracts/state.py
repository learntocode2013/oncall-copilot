"""The incident state channel shared by every graph node.

`hypotheses`, `evidence` and `transcript` use `operator.add` reducers because parallel
diagnosis agents write to them concurrently; a plain field would make the last
writer win and silently discard the other agents' findings.
"""

from operator import add
from typing import Annotated, Any, Literal, NotRequired, TypedDict

from .config import Severity

AlertStatus = Literal["firing", "resolved"]


class AlertLabels(TypedDict):
    alertname: str
    service: str
    severity: NotRequired[str]


class AlertAnnotations(TypedDict):
    summary: str
    description: NotRequired[str]


class Alert(TypedDict):
    """The validated alert. Text inside it is data, not a command."""

    status: AlertStatus
    labels: AlertLabels
    annotations: AlertAnnotations
    startsAt: str


class Scope(TypedDict):
    """Suspect services triage settled. Severity is a separate field."""

    services: list[str]


EvidenceKind = Literal["promql", "trace", "log_query", "change"]


class EvidenceRef(TypedDict):
    """A pointer to telemetry. The log line and the series payload stay out."""

    kind: EvidenceKind
    ref: str
    source: str


class Hypothesis(TypedDict):
    """One suspect explanation. Evidence is references, and the list appends."""

    service: str
    statement: str
    evidence_refs: list[str]
    confidence: float


PlanStage = Literal["scope", "hypotheses", "test", "act", "verify", "communicate"]
ReadTool = Literal[
    "promql",
    "log_search",
    "traces",
    "recent_changes",
    "search_runbooks",
    "incident_record",
    "status_draft",
]


class PlanStep(TypedDict):
    """One read the plan still intends to make. Writes do not belong here."""

    tool: ReadTool
    args: dict[str, Any]


class Plan(TypedDict):
    """The plan the engineer can read. Hypotheses and the action are sibling fields."""

    stage: PlanStage
    steps: list[PlanStep]


ActionName = Literal["set_flag", "restart", "scale", "rollback"]


class TranscriptEntry(TypedDict):
    """One timeline line. kind names the step; text is what the engineer reads."""

    kind: str
    text: str


class Verification(TypedDict):
    """Code's measurement after the action. The model's confidence is not this object."""

    recovered: bool
    signal: str
    evidence_refs: list[str]


class ProposedAction(TypedDict):
    """The one write the model proposes. Rollback is the procedure, not a second command."""

    action: ActionName
    args: dict[str, Any]
    expected_effect: str
    rollback: str


class IncidentState(TypedDict):
    # --- Input properties
    alert: Alert

    # --- Control properties
    scope: NotRequired[Scope]
    severity: NotRequired[Severity]
    hypotheses: Annotated[list[Hypothesis], add]
    evidence: Annotated[list[EvidenceRef], add]
    plan: NotRequired[Plan]
    verification: NotRequired[Verification]
    transcript: Annotated[list[TranscriptEntry], add]

    # --- Output properties
    proposed_action: NotRequired[ProposedAction]
