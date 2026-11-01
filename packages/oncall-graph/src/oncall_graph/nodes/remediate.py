"""The approval, verify, and rollback path shared by the solo agent and the team.

The model proposes one allow-listed action. This code is what runs it. Approve
executes that payload only when its hash matches the interrupt. An edited
payload interrupts again. Reject returns without a shop write. A failed
verification rolls the action back in code.
"""

from __future__ import annotations

import copy
import hashlib
import json

from langgraph.types import interrupt

from ..planning.monitor import code_rollback
from ..planning.plan import default_rollback, validate_action
from ..state import transcript_lines

# The checkout alert fires above 5%. Recovery is a rate under that line.
RECOVERY_LINE = 0.05


def command_payload(action) -> dict:
    """The exact command the engineer approves: action, args, and expected effect."""
    return {
        "action": action["action"],
        "args": dict(action.get("args") or {}),
        "expected_effect": action.get("expected_effect") or "",
    }


def payload_hash(payload) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def apply_command(action, command):
    """A new command on top of the previous proposal. Invalid commands raise."""
    if not isinstance(command, dict):
        # ValueError, not TypeError: the approval node catches it and interrupts again.
        raise ValueError("command must be an object")  # noqa: TRY004
    if "action" not in command and isinstance(command.get("command"), dict):
        command = command["command"]
    name = command.get("action") or action["action"]
    args = command.get("args") if "args" in command else action.get("args")
    effect = command.get("expected_effect") or action.get("expected_effect")
    proposed = {"action": name, "args": args, "expected_effect": effect}
    if command.get("rollback") is None:
        proposed["rollback"] = default_rollback(name, args if isinstance(args, dict) else {})
    else:
        proposed["rollback"] = command["rollback"]
    return validate_action(proposed)


def _decision(resume):
    if isinstance(resume, str):
        return {"decision": resume.strip().lower()}
    if isinstance(resume, dict):
        decision = dict(resume)
        choice = decision.get("decision")
        if isinstance(choice, str):
            decision["decision"] = choice.strip().lower()
        return decision
    return {}


def approval(state):
    """Interrupt with the command, its expected effect, and the payload hash.

    Resume with ``Command(resume=...)``. Approve runs this payload only when
    the hash matches. A different command or a hash from an earlier payload
    interrupts again and does not write. Reject ends the run with no write.

    The node starts over on each resume, and earlier resume values are replayed
    in order, so an edit made on the previous pause is applied before the new
    decision is read.
    """
    current = state.get("proposed_action")
    if not current:
        return {
            "status": "REJECTED",
            "route": "reject",
            "why": "no action to approve",
            "approval_payload_hash": "",
            "transcript": transcript_lines("approval", "approval rejected"),
        }
    action = copy.deepcopy(current)
    error = None
    while True:
        payload = command_payload(action)
        digest = payload_hash(payload)
        shown = {
            "command": {"action": payload["action"], "args": payload["args"]},
            "expected_effect": payload["expected_effect"],
            "payload_hash": digest,
        }
        if error:
            shown["error"] = error
            error = None
        decision = _decision(interrupt(shown))
        supplied = decision.get("command")
        if (
            isinstance(supplied, dict)
            and decision.get("expected_effect")
            and "expected_effect" not in supplied
        ):
            supplied = {**supplied, "expected_effect": decision["expected_effect"]}
        if supplied:
            try:
                edited = apply_command(action, supplied)
            except ValueError as exc:
                error = str(exc)
                continue
            if payload_hash(command_payload(edited)) != digest:
                action = edited
                continue
        choice = decision.get("decision") or ""
        if choice in ("reject", "rejected"):
            return {
                "status": "REJECTED",
                "route": "reject",
                "why": "engineer rejected the action",
                "approval_payload_hash": "",
                "transcript": transcript_lines("approval", "approval rejected"),
            }
        if choice in ("approve", "approved"):
            claimed = decision.get("payload_hash")
            if claimed not in (None, "", digest):
                continue
            plan = dict(state.get("plan") or {})
            plan["stage"] = "act"
            return {
                "plan": plan,
                "stage": "act",
                "proposed_action": action,
                "approval_payload_hash": digest,
                "route": "execute",
                "status": "APPROVED",
                "why": "",
                "transcript": transcript_lines("approval", "approval matched"),
            }
        continue


def execute_action_code(state, shop):
    """Run the approved command. A different hash does not touch the shop."""
    action = state.get("proposed_action") or {}
    if not action:
        return {
            "status": "APPROVAL_MISMATCH",
            "route": "stop",
            "why": "no action to execute",
            "transcript": transcript_lines("execute", "execute refused"),
        }
    digest = payload_hash(command_payload(action))
    if digest != (state.get("approval_payload_hash") or ""):
        return {
            "status": "APPROVAL_MISMATCH",
            "route": "stop",
            "why": "approval hash does not match the command",
            "transcript": transcript_lines("execute", "execute refused"),
        }
    result = shop.apply(action["action"], action["args"])
    plan = dict(state.get("plan") or {})
    plan["stage"] = "verify"
    return {
        "plan": plan,
        "stage": "verify",
        "last": {
            "step": {"tool": action["action"], "args": dict(action["args"])},
            "result": result,
        },
        "ok": True,
        "route": "verify",
        "transcript": transcript_lines("execute", f"executed {action['action']}"),
    }


def verify_metrics(state, shop):
    """Read the checkout error rate after the action. The model does not decide."""
    reading = shop.metrics()
    rate = float(reading.get("error_rate") or 0)
    recovered = rate < RECOVERY_LINE
    plan = dict(state.get("plan") or {})
    plan["stage"] = "communicate" if recovered else "verify"
    return {
        "plan": plan,
        "stage": plan["stage"],
        "verification": {
            "recovered": recovered,
            "signal": "checkout_error_rate",
            "evidence_refs": ["checkout_error_rate"],
        },
        "route": "recovered" if recovered else "rollback",
        "status": "RECOVERED" if recovered else (state.get("status") or "RUNNING"),
        "why": "" if recovered else "verification failed",
        "transcript": transcript_lines("verify", f"verify recovered={recovered}"),
    }


def rollback_code(state, shop):
    """Apply the inverse action in code. A prose rollback is recorded, not shelled."""
    pending = code_rollback(state)
    if pending.get("tool"):
        result = shop.apply(pending["tool"], pending.get("args") or {})
    else:
        result = {"ok": False, "skipped": True}
    return {
        "pending_rollback": pending,
        "last": {"step": pending, "result": result},
        "route": "replan",
        "transcript": transcript_lines("rollback", "rollback"),
    }


def route_approval(state) -> str:
    if state.get("route") == "execute" and state.get("approval_payload_hash"):
        return "execute"
    return "reject"


def route_execute(state) -> str:
    if state.get("status") == "APPROVAL_MISMATCH" or state.get("route") == "stop":
        return "stop"
    return "verify"


def route_verify(state) -> str:
    if (state.get("verification") or {}).get("recovered") is True:
        return "recovered"
    return "rollback"
