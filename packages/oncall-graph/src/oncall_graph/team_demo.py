"""Run the team graph against the scripted shop.

Every read, the approval write, and verification stay on the scripted
``Shop``. Nothing in this module calls the Astronomy Shop lab. The commander
and the specialists still call OpenRouter.

    export OPENROUTER_API_KEY=...
    uv run python -m oncall_graph.team_demo
"""

from __future__ import annotations

import json

from .stubs import Shop
from .topologies.c1_single import interrupt_value
from .topologies.c2_team import Team

PAYMENT_ALERT = {
    "status": "firing",
    "labels": {"alertname": "PaymentServiceFailed", "service": "payment"},
    "annotations": {"summary": "payment service failed"},
    "startsAt": "2026-11-06T00:00:00Z",
}

CHECKOUT_ALERT = {
    "status": "firing",
    "labels": {"alertname": "CheckoutErrorRateHigh", "service": "checkout"},
    "annotations": {"summary": "checkout error rate above 5% for five minutes"},
    "startsAt": "2026-11-06T00:00:00Z",
}


def print_trace(trace) -> None:
    """The path and the meter for this invoke. ``graph_path`` is the whole thread."""
    print(json.dumps(trace, indent=2, ensure_ascii=False))


def main() -> None:
    shop = Shop()
    # Several specialists share this budget. Diagnosis of one suspect may use
    # all of it, so 10 leaves room for triage, the runbook, and the proposal.
    team = Team(shop=shop, tool_budget=10)
    paused, measured = team.run(CHECKOUT_ALERT, incident_id="scripted-team")
    intr = interrupt_value(paused)
    print("status:", paused.get("status"))
    print("scope:", paused.get("scope"))
    print("severity:", paused.get("severity"))
    print("hypotheses:", paused.get("hypotheses"))
    print("diagnosis:", paused.get("diagnosis_reports"))
    print("action:", paused.get("proposed_action"))
    print("interrupt:", intr)
    print("trace:")
    print_trace(measured.trace)
    if intr is None:
        return
    choice = input("approve or reject? ").strip().lower()
    if choice not in ("approve", "approved"):
        done, measured = team.run(resume={"decision": "reject"}, incident_id="scripted-team")
    else:
        done, measured = team.run(
            resume={"decision": "approve", "payload_hash": intr["payload_hash"]},
            incident_id="scripted-team",
        )
    print("final status:", done.get("status"))
    print("verification:", done.get("verification"))
    print("shop writes:", shop.writes)
    print("trace:")
    print_trace(measured.trace)


if __name__ == "__main__":
    main()
