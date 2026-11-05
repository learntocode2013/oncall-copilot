"""The tool contracts both sides of the P2/P3 seam depend on."""

import pytest
from oncall_contracts.tools import (
    ACTION_SCHEMAS,
    NO_ACTION,
    Approval,
    ApprovalRequest,
    fingerprint,
)


def test_set_flag_takes_exactly_a_flag_and_a_variant():
    """The corpus validator rejects anything else, and this is the definition it
    should be checked against rather than its own hand-written copy."""
    schema = ACTION_SCHEMAS["set_flag"]

    assert sorted(schema["properties"]) == ["flag", "variant"]
    assert sorted(schema["required"]) == ["flag", "variant"]


def test_set_flag_rejects_extra_arguments():
    """An open schema would let the agent pass a third argument ops-actions
    silently ignores, which reads as a fix that was applied."""
    assert ACTION_SCHEMAS["set_flag"]["additionalProperties"] is False


def test_no_action_has_no_schema():
    """There is no tool to call, so there are no arguments to check."""
    assert NO_ACTION not in ACTION_SCHEMAS


def test_every_declared_action_matches_the_corpus_vocabulary():
    """The guard in tests/test_guards.py enforces this against
    oncall_knowledge.schema; asserted here too so contracts fails on its own."""
    assert set(ACTION_SCHEMAS) == {"set_flag"}


# --- fingerprints --------------------------------------------------------


def test_the_same_call_fingerprints_the_same():
    args = {"flag": "paymentFailure", "variant": "off"}

    assert fingerprint("set_flag", args) == fingerprint("set_flag", dict(args))


def test_argument_order_does_not_matter():
    assert fingerprint("set_flag", {"a": 1, "b": 2}) == fingerprint("set_flag", {"b": 2, "a": 1})


def test_different_arguments_fingerprint_differently():
    """The reason fingerprints exist: an approval for one flag must not
    authorise another."""
    off = fingerprint("set_flag", {"flag": "paymentFailure", "variant": "off"})
    on = fingerprint("set_flag", {"flag": "paymentFailure", "variant": "on"})

    assert off != on


def test_the_tool_name_is_part_of_the_fingerprint():
    args = {"flag": "paymentFailure", "variant": "off"}

    assert fingerprint("set_flag", args) != fingerprint("unset_flag", args)


@pytest.mark.parametrize("empty", [None, {}])
def test_no_arguments_and_empty_arguments_agree(empty):
    assert fingerprint("drain", empty) == fingerprint("drain", {})


def test_a_request_exposes_its_own_fingerprint():
    request = ApprovalRequest(tool="set_flag", args={"flag": "f", "variant": "off"})

    assert request.fingerprint == fingerprint("set_flag", {"flag": "f", "variant": "off"})


def test_a_request_defaults_to_no_args_rather_than_failing():
    assert ApprovalRequest(tool="drain").args == {}


def test_a_request_carries_the_rollback_and_the_runbook():
    """A human cannot sensibly approve an action whose undo is unknown, and an
    action with no runbook behind it is a guess the approver should see as one."""
    request = ApprovalRequest(
        tool="set_flag", args={}, runbook_id="payment-failure", rollback="set_flag on"
    )

    assert request.rollback == "set_flag on"
    assert request.runbook_id == "payment-failure"


def test_an_approval_defaults_to_granted():
    assert Approval(fingerprint="x", approved_by="oncall").granted is True


def test_an_approval_can_be_a_refusal():
    assert Approval(fingerprint="x", approved_by="oncall", granted=False).granted is False
