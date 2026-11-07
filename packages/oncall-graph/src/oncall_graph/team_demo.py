"""Run the team graph against the scripted shop, or the lab's telemetry.

By default every read, the approval write, and verification stay on the
scripted ``Shop``, and nothing calls the Astronomy Shop lab. ``--live`` points
``promql``, ``log_search``, and ``traces`` at the lab through ``LiveShop``; the
write and verification are still scripted, armed with ``--fault``. The
commander and the specialists call OpenRouter either way.

    export OPENROUTER_API_KEY=...
    uv run python -m oncall_graph.team_demo
    ASTRONOMY_SHOP_HOST=localhost uv run python -m oncall_graph.team_demo --live \
        --fault paymentUnreachable
"""

from __future__ import annotations

import argparse
import json

from .config import LIVE_TOOL_BUDGET
from .stubs import Shop
from .topologies.c1_single import interrupt_value
from .topologies.c2_team import Team

PAYMENT_ALERT = {
    "status": "firing",
    "labels": {"alertname": "PaymentServiceFailed", "service": "payment"},
    "annotations": {"summary": "Fail payment service charge requests n%"},
    "startsAt": "2026-11-06T00:00:00Z",
}

PAYMENT_UNAVAILABLE_ALERT = {
    "status": "firing",
    "labels": {"alertname": "PaymentServiceUnavailable", "service": "payment"},
    "annotations": {"summary": "Payment service is unavailable"},
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


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Run the team graph on one alert.")
    parser.add_argument("--live", action="store_true", help="read the lab's telemetry")
    parser.add_argument(
        "--fault",
        choices=sorted(Shop.SPAN_METRICS),
        default="paymentFailure",
        help="the fault the scripted write side is armed with",
    )
    args = parser.parse_args(argv)
    if args.live:
        from .astronomy_shop import LiveShop

        shop = LiveShop(fault=args.fault)
        # Triage and every diagnosis copy share this budget on real telemetry.
        team = Team(shop=shop, tool_budget=LIVE_TOOL_BUDGET)
    else:
        shop = Shop(fault=args.fault)
        # Several specialists share this budget. Diagnosis of one suspect may use
        # all of it, so 10 leaves room for triage, the runbook, and the proposal.
        team = Team(shop=shop, tool_budget=10)
    incident = "live-team" if args.live else "scripted-team"
    paused, measured = team.run(PAYMENT_UNAVAILABLE_ALERT, incident_id=incident)
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
        done, measured = team.run(resume={"decision": "reject"}, incident_id=incident)
    else:
        done, measured = team.run(
            resume={"decision": "approve", "payload_hash": intr["payload_hash"]},
            incident_id=incident,
        )
    print("final status:", done.get("status"))
    print("verification:", done.get("verification"))
    print("shop writes:", shop.writes)
    print("trace:")
    print_trace(measured.trace)


if __name__ == "__main__":
    main()
