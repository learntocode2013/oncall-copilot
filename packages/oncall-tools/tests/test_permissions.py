"""Write isolation, layer one: the tool never reaches the role's registry.

Layer two is hooks/pre_tool.py, tested next door. Both exist because in HTTP
mode mcp-ops is just another container on the Compose network -- reachability is
not the control.
"""

import pytest
from oncall_tools.permissions import (
    ROLES,
    WriteToolDenied,
    assert_read_only,
    for_role,
    write_tools,
)
from oncall_tools.registry import ToolRegistry, ToolSpec


def spec(name, *, write):
    return ToolSpec(name, name, {}, write=write, server="fake", invoke=lambda *_a, **_k: None)


@pytest.fixture
def mixed():
    """A read and a write, which is what a registry over all four servers will
    look like once ops_actions exists."""
    return ToolRegistry([spec("search_runbooks", write=False), spec("set_flag", write=True)])


def test_diagnosis_never_holds_a_write_tool(mixed):
    """The graded requirement: no diagnosis agent's registry contains a tool
    marked write. The model cannot try what it cannot see."""
    assert for_role(mixed, "diagnosis").names == ["search_runbooks"]


def test_remediation_holds_both(mixed):
    """Holding a write tool is not permission to call it -- pre_tool still
    requires a matching human approval."""
    assert sorted(for_role(mixed, "remediation").names) == ["search_runbooks", "set_flag"]


def test_an_unknown_role_raises(mixed):
    with pytest.raises(ValueError, match="supervisor"):
        for_role(mixed, "supervisor")


def test_both_roles_are_named(mixed):
    assert set(ROLES) == {"diagnosis", "remediation"}


def test_assert_read_only_names_the_offenders(mixed):
    with pytest.raises(WriteToolDenied, match="set_flag"):
        assert_read_only(mixed)


def test_assert_read_only_passes_on_a_diagnosis_view(mixed):
    assert_read_only(for_role(mixed, "diagnosis"))


def test_a_role_view_does_not_close_the_shared_sessions():
    """A role view is a lens, not a second connection pool."""
    closed = []
    registry = ToolRegistry([spec("t", write=False)], close=lambda: closed.append(True))

    for_role(registry, "diagnosis").close()

    assert closed == []


def test_write_tools_lists_what_needs_approval(mixed):
    assert [tool.name for tool in write_tools(mixed)] == ["set_flag"]


def test_a_diagnosis_view_of_a_write_only_server_is_empty_not_an_error():
    """ops_actions serves nothing a diagnosis agent may hold. Empty is the
    correct answer, not a failure."""
    assert len(for_role(ToolRegistry([spec("set_flag", write=True)]), "diagnosis")) == 0
