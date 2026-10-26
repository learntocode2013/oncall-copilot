"""Names shared by the graph, the harness and the dashboards.

`Topology` selects which of the three E1 configurations `build_graph` compiles.
It lives here rather than in `oncall-graph` so the evaluation harness can name
a configuration without importing the package it is measuring.
"""

from typing import Literal

# C0: fixed workflow, no agent decisions. C1: one agent, all tools.
# C2: supervisor plus specialists. E1 compares all three on the same model,
# tools and budget, so all three must stay runnable for the life of the project.
Topology = Literal["c0_workflow", "c1_single", "c2_team"]

# Derived from how many services the injected fault actually spans. The unit of
# stratification for any claim that the team helps on complex incidents but
# costs more on simple ones.
Complexity = Literal["single", "multi"]
