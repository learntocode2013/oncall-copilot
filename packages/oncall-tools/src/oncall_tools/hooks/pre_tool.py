"""The gate every tool call passes through before it runs.

A read passes. A write runs only with a human approval that matches THE EXACT
ARGUMENTS about to be used -- approval binding to the precise payload is a
live-demo requirement, and an approval that bound only to a tool name would
authorise `set_flag adFailure on` because someone approved
`set_flag paymentFailure off`.

`interrupt()` is a LangGraph primitive, so the call site that pauses the graph
and shows the request to a human lives in `oncall_graph/nodes/remediate.py`
(P2). What that site constructs and what this gate checks is
`oncall_contracts.tools.ApprovalRequest`, which is why the payload type lives in
contracts rather than in either package.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from oncall_contracts.tools import Approval, ApprovalRequest, fingerprint

from oncall_tools.registry import ToolSpec


class ApprovalRequired(PermissionError):
    """A write tool was called with no approval.

    Carries the `request`, so the node that catches this has exactly what it
    needs to pass to `interrupt()` -- the caller does not have to rebuild it.
    """

    def __init__(self, request: ApprovalRequest) -> None:
        self.request = request
        super().__init__(f"{request.tool} needs approval before it can run")


class ApprovalMismatch(PermissionError):
    """An approval was presented for a different call than the one about to run.

    This is the interesting failure. It fires when the arguments changed between
    asking and acting -- a re-planned fix, a retry with a different flag, or a
    replayed approval -- and in every one of those cases running the tool would
    apply something nobody approved.
    """

    def __init__(self, tool: str, expected: str, presented: str) -> None:
        self.tool = tool
        self.expected = expected
        self.presented = presented
        super().__init__(
            f"approval for {tool} does not match the call: "
            f"expected {expected[:12]}, got {presented[:12]}"
        )


class ApprovalDenied(PermissionError):
    """A human was asked and said no. Distinct from never having asked."""

    def __init__(self, tool: str, approved_by: str) -> None:
        self.tool = tool
        self.approved_by = approved_by
        super().__init__(f"{approved_by} declined {tool}")


def pre_tool(
    spec: ToolSpec,
    args: Mapping[str, Any] | None = None,
    *,
    approval: Approval | None = None,
    request: ApprovalRequest | None = None,
) -> None:
    """Return quietly if the call may proceed; raise otherwise.

    A hook, not a wrapper: it returns `None` on success so the caller keeps
    control of how the tool is actually invoked, and every refusal is a distinct
    exception type so a node can tell "ask the human" from "the human said no"
    from "this approval is stale".
    """
    if spec.read:
        return

    if approval is None:
        raise ApprovalRequired(request or ApprovalRequest(tool=spec.name, args=dict(args or {})))

    if not approval.granted:
        raise ApprovalDenied(spec.name, approval.approved_by)

    expected = fingerprint(spec.name, args)
    if approval.fingerprint != expected:
        raise ApprovalMismatch(spec.name, expected, approval.fingerprint)
