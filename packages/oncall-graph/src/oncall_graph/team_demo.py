"""Run the team graph against the scripted shop.

Every read, the approval write, and verification stay on the scripted
``Shop``. Nothing in this module calls the Astronomy Shop lab. The commander
and the specialists still call OpenRouter.

    export OPENROUTER_API_KEY=...
    uv run python -m oncall_graph.team_demo
"""

from __future__ import annotations

from .stubs import Shop
from .topologies.c1_single import interrupt_value
from .topologies.c2_team import Team

ALERT = "payment service failed"


def main() -> None:
    shop = Shop()
    # Several specialists share this budget. Diagnosis of one suspect may use
    # all of it, so 10 leaves room for triage, the runbook, and the proposal.
    team = Team(shop=shop, tool_budget=10)
    paused, _measured = team.run(ALERT, incident_id="scripted-team")
    intr = interrupt_value(paused)
    print("status:", paused.get("status"))
    print("scope:", paused.get("scope"))
    print("severity:", paused.get("severity"))
    print("hypotheses:", paused.get("hypotheses"))
    print("diagnosis:", paused.get("diagnosis_reports"))
    print("action:", paused.get("proposed_action"))
    print("interrupt:", intr)
    if intr is None:
        return
    choice = input("approve or reject? ").strip().lower()
    if choice not in ("approve", "approved"):
        done, _measured = team.run(resume={"decision": "reject"}, incident_id="scripted-team")
    else:
        done, _measured = team.run(
            resume={"decision": "approve", "payload_hash": intr["payload_hash"]},
            incident_id="scripted-team",
        )
    print("final status:", done.get("status"))
    print("verification:", done.get("verification"))
    print("shop writes:", shop.writes)


if __name__ == "__main__":
    main()
