"""The incident state channel shared by every graph node.

`hypotheses`, `evidence` and `transcript` use `operator.add` reducers because parallel
diagnosis agents write to them concurrently; a plain field would make the last
writer win and silently discard the other agents' findings.
"""

from operator import add
from typing import Annotated, Any, Literal, NotRequired, TypedDict

Severity = Literal["sev1", "sev2", "sev3", "none"]


class Hypothesis(TypedDict):
    service: str
    statement: str
    evidence_refs: list[str]
    confidence: float


class ProposedAction(TypedDict):
    action: str
    args: dict[str, Any]
    expected_effect: str
    rollback: str


class IncidentState(TypedDict):
    # --- Input properties
    alert: dict[str, Any]

    # --- Control properties
    scope: NotRequired[dict[str, Any]]
    severity: NotRequired[Severity]
    hypotheses: Annotated[list[Hypothesis], add]
    evidence: Annotated[list[dict[str, Any]], add]
    plan: NotRequired[list[str]]
    verification: NotRequired[dict[str, Any]]
    transcript: Annotated[list[dict[str, Any]], add]

    # --- Output properties
    proposed_action: NotRequired[ProposedAction]
