"""The only write server, against a throwaway flagd document.

Every test points FLAGD_CONFIG at a tmp_path copy of the real
deploy/flagd/demo.flagd.json, so the tests exercise the real flag definitions
and real corpus pairs without touching the file flagd actually watches.
"""

import json
import shutil
from pathlib import Path

import pytest
from oncall_contracts.flagd import current_variant, read_document
from oncall_tools.servers.ops_actions import (
    FLAG_PATH_ENV,
    ActionNotAllowed,
    _allowed,
    build_server,
    rollback_flag,
    set_flag,
)

REAL = Path(__file__).resolve().parents[3] / "deploy" / "flagd" / "demo.flagd.json"


@pytest.fixture(autouse=True)
def flag_file(tmp_path, monkeypatch):
    """A copy of the real document. The corpus names real flags and variants, so
    a synthetic fixture would test an allowlist against flags that do not exist."""
    path = tmp_path / "demo.flagd.json"
    shutil.copy(REAL, path)
    monkeypatch.setenv(FLAG_PATH_ENV, str(path))
    _allowed.cache_clear()
    yield path
    _allowed.cache_clear()


# --- the allowlist -------------------------------------------------------


def test_the_corpus_splits_cleanly_into_fixes_and_rollbacks():
    """The finding the two-tool split rests on: every fix turns a flag off and
    every rollback re-arms, with no pair in both."""
    fixes, rollbacks = _allowed()

    assert {variant for _flag, variant in fixes} == {"off"}
    assert {variant for _flag, variant in rollbacks} == {"on", "10sec", "100%", "1000x"}
    assert not fixes & rollbacks


def test_both_sets_are_non_empty():
    """An empty allowlist would make every test below pass for the wrong reason."""
    fixes, rollbacks = _allowed()

    assert fixes
    assert rollbacks


# --- set_flag applies fixes ---------------------------------------------


def arm(flag_file, flag, variant):
    """The real document ships with every fault DISARMED, so a fix applied to an
    untouched copy is correctly a no-op. A test about applying a fix has to arm
    the fault first -- otherwise it passes while asserting nothing."""
    document = json.loads(flag_file.read_text())
    document["flags"][flag]["defaultVariant"] = variant
    flag_file.write_text(json.dumps(document))


def test_set_flag_turns_a_fault_off(flag_file):
    arm(flag_file, "paymentFailure", "100%")

    result = set_flag("paymentFailure", "off")

    assert result["changed"] is True
    assert result["variant"] == "off"
    assert current_variant(read_document(flag_file), "paymentFailure") == "off"


def test_set_flag_reports_the_previous_variant(flag_file):
    """What the approver needs to judge the change, and what a rollback needs."""
    arm(flag_file, "paymentFailure", "100%")

    assert set_flag("paymentFailure", "off")["previous"] == "100%"


def test_a_flag_already_off_reports_no_change_rather_than_success(flag_file):
    """`changed: false` is the honest answer. Reporting success for a no-op is
    how a fix that did nothing reads as a fix that worked."""
    arm(flag_file, "paymentFailure", "100%")
    assert set_flag("paymentFailure", "off")["changed"] is True

    assert set_flag("paymentFailure", "off")["changed"] is False


# --- what set_flag refuses ----------------------------------------------


def test_set_flag_refuses_to_arm_a_fault():
    """The whole reason for two tools. `paymentFailure -> 100%` is a real
    rollback pair in the corpus, and set_flag must still refuse it."""
    with pytest.raises(ActionNotAllowed, match="fix"):
        set_flag("paymentFailure", "100%")


def test_set_flag_refuses_a_variant_no_runbook_names():
    """`50%` exists in flagd but no runbook names it, so the agent cannot
    invent a severity."""
    with pytest.raises(ActionNotAllowed, match="50%"):
        set_flag("paymentFailure", "50%")


def test_set_flag_refuses_a_flag_no_runbook_knows():
    """16 of the 18 demo flags do have a runbook fix. `emitRawPii` is one of the
    two that do not, and it is the interesting one: a flag that leaks personal
    data is exactly what an agent must not be able to reach."""
    with pytest.raises(ActionNotAllowed, match="emitRawPii"):
        set_flag("emitRawPii", "off")


def test_a_refusal_says_what_would_be_accepted():
    """ "Not allowed" with no alternative invites the agent to guess variants."""
    with pytest.raises(ActionNotAllowed, match=r"\['off'\]"):
        set_flag("paymentFailure", "50%")


def test_a_refusal_changes_nothing(flag_file):
    before = flag_file.read_text()

    with pytest.raises(ActionNotAllowed):
        set_flag("paymentFailure", "50%")

    assert flag_file.read_text() == before


# --- rollback_flag ------------------------------------------------------


def test_rollback_restores_a_fault_variant(flag_file):
    set_flag("paymentFailure", "off")

    result = rollback_flag("paymentFailure", "100%")

    assert result["kind"] == "rollback"
    assert current_variant(read_document(flag_file), "paymentFailure") == "100%"


def test_rollback_refuses_a_fix_pair():
    """Symmetric to set_flag refusing a rollback: turning a flag off is a fix,
    and routing it through the rollback tool would hide the intent."""
    with pytest.raises(ActionNotAllowed, match="rollback"):
        rollback_flag("paymentFailure", "off")


def test_rollback_refuses_a_variant_no_runbook_names():
    with pytest.raises(ActionNotAllowed):
        rollback_flag("paymentFailure", "75%")


# --- the server ---------------------------------------------------------


def test_the_server_registers_exactly_the_two_tools():
    import anyio

    tools = anyio.run(build_server().list_tools)

    assert {tool.name for tool in tools} == {"set_flag", "rollback_flag"}


def test_both_tools_are_annotated_as_writes():
    """registry._is_write reads this, and permissions.for_role uses it to keep
    them out of every diagnosis registry."""
    import anyio

    for tool in anyio.run(build_server().list_tools):
        assert tool.annotations is not None, tool.name
        assert tool.annotations.read_only_hint is False, tool.name
        assert tool.annotations.destructive_hint is True, tool.name


def test_the_instructions_say_this_is_the_write_server():
    instructions = build_server().instructions

    assert "ONLY WRITE SERVER" in instructions
    assert "RE-ARMS" in instructions


def test_the_rollback_tool_warns_that_it_re_arms():
    """The description is what the model reads. If it does not say the tool
    re-arms the fault, the model will treat it as a generic undo."""
    import anyio

    rollback = next(t for t in anyio.run(build_server().list_tools) if t.name == "rollback_flag")

    assert "RE-ARMS" in rollback.description


def test_build_server_fails_on_an_unreadable_flag_file(monkeypatch, tmp_path):
    """A write server that starts without its target binds a port and fails on
    the one call that matters."""
    from oncall_contracts.flagd import FlagFileError

    monkeypatch.setenv(FLAG_PATH_ENV, str(tmp_path / "absent.json"))
    _allowed.cache_clear()

    with pytest.raises(FlagFileError):
        build_server()
