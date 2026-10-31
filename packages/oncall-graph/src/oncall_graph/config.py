"""Model ids shared by the solo agent and the team.

These ids live in the graph package until the shared model config exists.
The commander uses a stronger model than the specialists. In the L11 lab,
gpt-4o-mini was unreliable as a coordinator and gpt-4.1-mini worked. Both
designs keep these ids, and every meter row records the id it actually called.

Tool-call budget is configuration. The E4 ablation uses 5, 10, or 20.
"""

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
SPECIALIST_MODEL = "openai/gpt-4o-mini"
COMMANDER_MODEL = "openai/gpt-4.1-mini"

TOOL_BUDGETS = (5, 10, 20)
DEFAULT_TOOL_BUDGET = 10
DEFAULT_MAX_REVISIONS = 3
