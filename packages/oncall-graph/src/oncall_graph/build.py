"""Compile one of the three E1 configurations.

``c1_single`` is the solo baseline. ``c0_workflow`` and ``c2_team`` are later
topologies and must stay unimplemented until those graphs are added beside
this one, not by editing it.
"""

from typing import Any

from langgraph.graph.state import CompiledStateGraph
from oncall_contracts.config import Topology

from .config import DEFAULT_MAX_REVISIONS, DEFAULT_TOOL_BUDGET
from .stubs import Shop
from .topologies.c1_single import Solo


def build_graph(
    tools: dict[str, Any] | Shop | None = None,
    store: Any = None,
    *,
    topology: Topology = "c2_team",
    model: Any = None,
    tool_budget: int = DEFAULT_TOOL_BUDGET,
    max_revisions: int = DEFAULT_MAX_REVISIONS,
    checkpointer: Any = None,
) -> CompiledStateGraph:
    """Compile ``topology``.

    ``tools`` is the injected tool bundle. ``None`` or ``{}`` selects the
    scripted shop, which is what the solo baseline runs against today. A
    non-empty tool dict is refused until the MCP registry is wired in.
    ``store`` is accepted for the long-term memory handle and is not read
    by this baseline. ``model`` is a ready-made chat model, or omitted to
    build the specialist OpenRouter client. Omitting it requires
    ``OPENROUTER_API_KEY`` while the graph is compiled, because the agent
    binds that client in its constructor.
    """
    if topology == "c1_single":
        if isinstance(tools, Shop):
            shop = tools
        elif tools in (None, {}):
            shop = Shop()
        else:
            raise NotImplementedError(
                "c1_single still runs the scripted shop. "
                "Pass tools=None until MCP tools are injected."
            )
        solo = Solo(
            model=model,
            shop=shop,
            tool_budget=tool_budget,
            max_revisions=max_revisions,
            checkpointer=checkpointer,
        )
        return solo.graph
    if topology in ("c0_workflow", "c2_team"):
        raise NotImplementedError(f"{topology} is not implemented. c1_single is the solo baseline.")
    raise ValueError(f"unknown topology: {topology!r}")
