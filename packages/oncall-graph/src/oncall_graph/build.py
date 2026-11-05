"""Compile one of the three E1 configurations.

``c1_single`` is the solo baseline. ``c2_team`` is the commander and the
specialists. ``c0_workflow`` is still later, and stays unimplemented beside
these two rather than by editing either of them.
"""

from typing import Any

from langgraph.graph.state import CompiledStateGraph
from oncall_contracts.config import Topology

from .config import DEFAULT_MAX_REVISIONS, DEFAULT_TOOL_BUDGET
from .stubs import Shop
from .topologies.c1_single import Solo
from .topologies.c2_team import Team


def build_graph(
    tools: dict[str, Any] | Shop | None = None,
    store: Any = None,
    *,
    topology: Topology = "c2_team",
    model: Any = None,
    tool_budget: int = DEFAULT_TOOL_BUDGET,
    max_revisions: int = DEFAULT_MAX_REVISIONS,
    checkpointer: Any = None,
    parallel_diagnosis: bool = True,
) -> CompiledStateGraph:
    """Compile ``topology``.

    ``tools`` is the injected tool bundle. ``None`` or ``{}`` selects the
    scripted shop. A non-empty tool dict is refused until the MCP registry
    is wired in.     ``store`` is the lesson store the runbook step filters.
    ``c1_single`` does not read it. ``model`` is a ready-made chat model.
    Omit it and an agent builds its OpenRouter client, which needs
    ``OPENROUTER_API_KEY``. ``c1_single`` builds that client while compiling.
    ``c2_team`` builds it on the first specialist call. ``parallel_diagnosis``
    is the fan-out switch: false gives one diagnosis agent the whole suspect
    list and the whole tool budget.
    """
    if topology in ("c1_single", "c2_team"):
        if isinstance(tools, Shop):
            shop = tools
        elif tools in (None, {}):
            shop = Shop()
        else:
            raise NotImplementedError(
                f"{topology} still runs the scripted shop. "
                "Pass tools=None until MCP tools are injected."
            )
        if topology == "c1_single":
            solo = Solo(
                model=model,
                shop=shop,
                tool_budget=tool_budget,
                max_revisions=max_revisions,
                checkpointer=checkpointer,
            )
            return solo.graph
        team = Team(
            model=model,
            shop=shop,
            store=store,
            tool_budget=tool_budget,
            max_revisions=max_revisions,
            checkpointer=checkpointer,
            parallel_diagnosis=parallel_diagnosis,
        )
        return team.graph
    if topology == "c0_workflow":
        raise NotImplementedError("c0_workflow is not implemented.")
    raise ValueError(f"unknown topology: {topology!r}")
