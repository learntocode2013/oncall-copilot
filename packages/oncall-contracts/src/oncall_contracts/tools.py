"""The MCP tool contracts both sides of the P2/P3 seam depend on.

This module exists so that the approval payload and the action schemas can be
imported by the graph (which constructs them), by oncall-tools (which checks
them) and by the corpus validator (which checks runbook `fix` blocks against
them) without any of those three importing each other.

`oncall_knowledge.schema` carries a TODO asking for exactly this: its
`ACTION_NAMES` is a hand-written copy that "will drift". `ACTION_SCHEMAS` here
is the definition that copy should be checked against, and
`test_actions_match_the_tool_schemas` in tests/test_guards.py does the checking.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

#: Argument schemas for every action a runbook may name in its `fix` block.
#:
#: JSON Schema rather than a dataclass per action, because this is what the MCP
#: tool declares on the wire: the registry can compare a server's advertised
#: `input_schema` against the entry here and find a server that has drifted from
#: the contract.
#:
#: `additionalProperties: False` is deliberate. The validator already rejects a
#: set_flag whose args are not exactly {flag, variant}; an open schema here
#: would let the agent pass a third argument that ops-actions silently ignores,
#: which reads as a fix that was applied.
ACTION_SCHEMAS: Mapping[str, Mapping[str, Any]] = {
    "set_flag": {
        "type": "object",
        "properties": {
            "flag": {"type": "string", "minLength": 1},
            "variant": {"type": "string", "minLength": 1},
        },
        "required": ["flag", "variant"],
        "additionalProperties": False,
    },
}

#: The action a runbook names when it has no automated fix. It is not in
#: ACTION_SCHEMAS because there is no tool to call and so no arguments to check.
NO_ACTION = "none"


def fingerprint(tool: str, args: Mapping[str, Any] | None) -> str:
    """Identify one exact tool call.

    An approval has to bind to the payload that will actually run, not to the
    tool's name -- otherwise an approval for `set_flag paymentFailure off` also
    authorises `set_flag adFailure on`, and the live-demo approval gate would be
    theatre. Keys are sorted so that two orderings of the same arguments agree.
    """
    canonical = json.dumps(args or {}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{tool}\x00{canonical}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """What the graph shows a human when a write tool wants to run.

    Constructed at the `interrupt()` call site in oncall_graph/nodes/remediate.py
    (P2) and checked by oncall_tools.hooks.pre_tool (P3). It carries `rollback`
    because a human cannot sensibly approve an action whose undo is unknown, and
    `runbook_id` because an action with no runbook behind it is a guess the
    approver should see as one.
    """

    tool: str
    args: Mapping[str, Any] = field(default_factory=dict)
    reason: str = ""
    runbook_id: str | None = None
    rollback: str | None = None

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.tool, self.args)


@dataclass(frozen=True, slots=True)
class Approval:
    """A human's answer to one ApprovalRequest.

    `fingerprint` is the request's, not the tool's. pre_tool compares it against
    the call about to run and refuses a mismatch, so an approval cannot be
    reused for different arguments.
    """

    fingerprint: str
    approved_by: str
    granted: bool = True
