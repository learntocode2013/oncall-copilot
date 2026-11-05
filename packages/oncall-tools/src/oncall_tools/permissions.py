"""Which role may hold which tools.

Write isolation is enforced HERE and in `hooks/pre_tool.py`, not by network
topology. In HTTP mode `mcp-ops` is just another container on the Compose
network and any agent could reach it; what stops a diagnosis agent calling it is
that `for_role` never puts the tool in its registry, and that `pre_tool` refuses
an unapproved write even if one got there.

Both layers exist on purpose. `for_role` means the model never sees the tool, so
it cannot try; `pre_tool` means that if a future topology hands the wrong
registry to the wrong agent, the call still fails. One is prevention, the other
is the backstop that catches the prevention being wrong.
"""

from __future__ import annotations

from typing import Literal

from oncall_tools.registry import ToolRegistry, ToolSpec

#: `diagnosis` covers every agent that investigates -- the solo baseline, the
#: supervisor and all C2 specialists except the remediator. `remediation` is the
#: one role allowed to hold a write tool, and holding it still does not mean
#: calling it: pre_tool requires a matching human approval.
Role = Literal["diagnosis", "remediation"]

ROLES: tuple[Role, ...] = ("diagnosis", "remediation")


class WriteToolDenied(PermissionError):
    """A write tool reached a role that may not hold one.

    Raised by `assert_read_only`, which exists so a caller can check a registry
    it did not build -- the guard in tests/test_guards.py uses it.
    """

    def __init__(self, role: str, offenders: list[str]) -> None:
        self.role = role
        self.offenders = offenders
        super().__init__(f"{role} registry holds write tool(s): {offenders}")


def for_role(registry: ToolRegistry, role: Role) -> ToolRegistry:
    """Narrow `registry` to what `role` may hold.

    The returned registry shares the underlying sessions, so closing the
    original closes this one too, and this one's `close()` is a no-op. That is
    deliberate: a role view is a lens, not a second connection pool.
    """
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}; expected one of {list(ROLES)}")
    if role == "remediation":
        return registry.select(lambda _tool: True)
    return registry.reads()


def assert_read_only(registry: ToolRegistry, *, role: str = "diagnosis") -> None:
    """Raise unless every tool in `registry` is a read.

    The graded requirement is a test asserting no diagnosis agent's registry
    contains a tool marked write. This is the assertion that test makes, kept
    next to the code it checks rather than written out in the test, so a change
    to the rule changes one place.
    """
    offenders = sorted(tool.name for tool in registry if tool.write)
    if offenders:
        raise WriteToolDenied(role, offenders)


def write_tools(registry: ToolRegistry) -> list[ToolSpec]:
    """Every write tool, for the approval gate and for diagnostics."""
    return list(registry.writes())
