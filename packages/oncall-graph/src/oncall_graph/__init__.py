"""P2 — incident commander. `build_graph` is this package's only public name."""

from typing import Any

from oncall_contracts.config import Topology

__all__ = ["build_graph"]


def build_graph(
    tools: dict[str, Any] | None = None,
    store: Any = None,
    *,
    topology: Topology = "c2_team",
) -> Any:
    """Compile one of the three E1 configurations.

    `topology` selects it: `c0_workflow` (fixed sequence, no agent decisions),
    `c1_single` (one ReAct loop holding all tools) or `c2_team` (supervisor
    plus specialists). Tools arrive by injection. The solo baseline implements
    `c1_single`; the other two topologies are later work.
    """
    raise NotImplementedError(
        "P2: c1_single lands with the solo baseline; c0_workflow and c2_team are later topologies"
    )
