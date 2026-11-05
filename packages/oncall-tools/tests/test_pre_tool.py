"""Write isolation, layer two: the call is refused even if the tool got through.

Approval binds to the EXACT arguments. An approval that bound only to a tool
name would authorise `set_flag adFailure on` because a human approved
`set_flag paymentFailure off`, which is the failure the live-demo approval
requirement exists to prevent.
"""

import pytest
from oncall_contracts.tools import Approval, ApprovalRequest, fingerprint
from oncall_tools.hooks.pre_tool import (
    ApprovalDenied,
    ApprovalMismatch,
    ApprovalRequired,
    pre_tool,
)
from oncall_tools.registry import ToolSpec

READ = ToolSpec("search_runbooks", "", {}, write=False, server="runbooks", invoke=lambda **_k: None)
WRITE = ToolSpec("set_flag", "", {}, write=True, server="ops", invoke=lambda **_k: None)
ARGS = {"flag": "paymentFailure", "variant": "off"}


def approval_for(tool, args, **kwargs):
    return Approval(fingerprint=fingerprint(tool, args), approved_by="oncall", **kwargs)


def test_a_read_needs_no_approval():
    pre_tool(READ, {"query": "checkout 422"})


def test_a_write_without_approval_raises():
    with pytest.raises(ApprovalRequired):
        pre_tool(WRITE, ARGS)


def test_the_raised_request_carries_what_the_human_must_see():
    """The node that catches this passes it straight to interrupt(); it should
    not have to rebuild the payload."""
    with pytest.raises(ApprovalRequired) as caught:
        pre_tool(WRITE, ARGS)

    assert caught.value.request.tool == "set_flag"
    assert caught.value.request.args == ARGS


def test_a_caller_supplied_request_is_preserved():
    """remediate.py builds the richer request -- with the runbook and the
    rollback -- and this gate must hand that one back, not a bare substitute."""
    request = ApprovalRequest(
        tool="set_flag", args=ARGS, runbook_id="payment-failure", rollback="set_flag on"
    )

    with pytest.raises(ApprovalRequired) as caught:
        pre_tool(WRITE, ARGS, request=request)

    assert caught.value.request.rollback == "set_flag on"
    assert caught.value.request.runbook_id == "payment-failure"


def test_a_matching_approval_lets_the_write_through():
    pre_tool(WRITE, ARGS, approval=approval_for("set_flag", ARGS))


def test_an_approval_for_different_arguments_is_refused():
    """The whole point. Approved paymentFailure off; attempting adFailure on."""
    approved = approval_for("set_flag", {"flag": "paymentFailure", "variant": "off"})

    with pytest.raises(ApprovalMismatch):
        pre_tool(WRITE, {"flag": "adFailure", "variant": "on"}, approval=approved)


def test_an_approval_for_a_different_tool_is_refused():
    with pytest.raises(ApprovalMismatch):
        pre_tool(WRITE, ARGS, approval=approval_for("restart_service", ARGS))


def test_argument_order_does_not_change_the_fingerprint():
    """Two orderings of the same call are the same call. Without sorted keys a
    re-planned fix would read as a different payload and be refused for no
    reason a human could understand."""
    reordered = {"variant": "off", "flag": "paymentFailure"}

    pre_tool(WRITE, reordered, approval=approval_for("set_flag", ARGS))


def test_a_declined_approval_is_distinct_from_never_asking():
    """A node must be able to tell "ask the human" from "the human said no":
    retrying the first is correct, retrying the second is not."""
    declined = approval_for("set_flag", ARGS, granted=False)

    with pytest.raises(ApprovalDenied, match="oncall"):
        pre_tool(WRITE, ARGS, approval=declined)


def test_an_extra_argument_changes_the_fingerprint():
    """An approved call plus one more argument is a different call. ops-actions
    may ignore the extra, and an ignored argument reads as a fix applied."""
    with pytest.raises(ApprovalMismatch):
        pre_tool(WRITE, {**ARGS, "force": True}, approval=approval_for("set_flag", ARGS))


def test_a_write_with_no_arguments_still_needs_approval():
    tool = ToolSpec("drain", "", {}, write=True, server="ops", invoke=lambda **_k: None)

    with pytest.raises(ApprovalRequired):
        pre_tool(tool, None)
