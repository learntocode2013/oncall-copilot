"""One record per (case, configuration) run. Written to artifacts/runs/.

This is the only source of numbers for the evaluation report: nobody retypes a
figure into a document. Grouping these by config_id x complexity produces the
E1 table directly.
"""

from typing import NotRequired, TypedDict

from .config import Complexity, Topology


class RunRecord(TypedDict):
    run_id: str
    case_id: str
    fault_id: str
    config_id: Topology
    complexity: Complexity

    # Cost side of E1 — what the orchestration charges you.
    tool_calls: int
    tokens: int
    usd: float
    latency_ms: int

    # Outcome side of E1.
    correct_root_cause: bool  # top-1
    root_cause_rank: int | None  # for top-3; None if never proposed
    correct_action_proposed: bool
    verified_recovery: bool
    harmful_actions: int  # target is zero
    rolled_back: bool

    # Provenance, so a result can be tied to what produced it.
    model_ids: dict[str, str]
    playbook_hash: NotRequired[str | None]
    error: NotRequired[str | None]
