"""Ground-truth contracts shared by the evaluator and the service.

The evaluator knows the true cause because it injects the fault.  Actions are
structured data rather than command-like strings so scoring never depends on
parsing prose.
"""

from typing import Any, Literal, TypedDict

from .config import Complexity


class FaultAction(TypedDict):
    """One typed operation used to inject, clean up, or remediate a fault."""

    action: str
    args: dict[str, Any]


SignalOperator = Literal[">", ">=", "<", "<=", "=="]


class FaultSignal(TypedDict):
    """A Prometheus query and the condition that proves a lifecycle phase."""

    query: str
    operator: SignalOperator
    threshold: float


class FaultSpec(TypedDict):
    """One fault family's stable ground truth.

    ``correct_action`` is ``None`` for a no-fault or no-remediation case where
    proposing any write would be harmful.
    """

    fault_id: str
    enable: FaultAction
    disable: FaultAction
    root_cause_services: list[str]
    complexity: Complexity
    correct_action: FaultAction | None
    detect: FaultSignal
    recovery: FaultSignal
