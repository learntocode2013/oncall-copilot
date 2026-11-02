"""Run the solo agent against the Astronomy Shop telemetry.

Pytest keeps the scripted shop. This module is the live demo: ``promql``,
``log_search``, and ``traces`` read the lab. The other tools, the approval
write, and verification still use the scripted shop.

    export OPENROUTER_API_KEY=...
    uv run python -m oncall_graph.solo_demo
"""

from __future__ import annotations

from .astronomy_shop import LiveShop
from .topologies.c1_single import Solo, interrupt_value

# ALERT = "checkout error rate above 5% for five minutes"
ALERT = "payment service failed"


def main() -> None:
    shop = LiveShop()
    solo = Solo(shop=shop, tool_budget=5)
    paused, _measured = solo.run(ALERT, incident_id="astronomy-demo")
    intr = interrupt_value(paused)
    print("status:", paused.get("status"))
    print("action:", paused.get("proposed_action"))
    print("interrupt:", intr)
    if intr is None:
        return
    choice = input("approve or reject? ").strip().lower()
    if choice not in ("approve", "approved"):
        done, _measured = solo.run(resume={"decision": "reject"}, incident_id="astronomy-demo")
    else:
        done, _measured = solo.run(
            resume={"decision": "approve", "payload_hash": intr["payload_hash"]},
            incident_id="astronomy-demo",
        )
    print("final status:", done.get("status"))
    print("verification:", done.get("verification"))
    print("shop writes:", shop.writes)


if __name__ == "__main__":
    main()
