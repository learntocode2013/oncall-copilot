# Every rejected plan raises ValueError. Callers catch that one type.
# ruff: noqa: TRY004
"""The visible incident plan, and the checks that keep it executable.

Valid JSON is not a valid plan. ``validate_plan`` rejects the whole proposal
when a tool is unknown, a command is shell-shaped, more than one action is
proposed, or an action has no expected effect. Nothing in a rejected plan runs.
"""

from __future__ import annotations

import json
import re

PLAN_STAGES = ("scope", "hypotheses", "test", "act", "verify", "communicate")

# Figure 5 tool names. Write tools are the only ones that change the shop.
READ_TOOLS = (
    "promql",
    "log_search",
    "traces",
    "recent_changes",
    "search_runbooks",
    "incident_record",
    "status_draft",
)
ACTION_TOOLS = ("set_flag", "restart", "scale", "rollback")
KNOWN_TOOLS = READ_TOOLS + ACTION_TOOLS

# Typed arguments the executor will accept. Totals and shell strings are not here.
ACTION_ARGS = {
    "set_flag": {"flag": str, "enabled": bool},
    "restart": {"service": str},
    "scale": {"service": str, "replicas": int},
    "rollback": {"service": str},
}


def default_rollback(tool, args):
    """The allow-listed inverse of one action, used when the model did not spell one out."""
    if tool not in ACTION_TOOLS:
        raise ValueError(f"unknown tool: {tool!r}")
    args = dict(args or {})
    if tool == "set_flag":
        enabled = args.get("enabled")
        flag = args.get("flag")
        if not isinstance(enabled, bool):
            raise ValueError("set_flag.enabled must be a boolean")
        if not isinstance(flag, str) or not flag.strip():
            raise ValueError("set_flag.flag must be a non-empty string")
        return {"tool": "set_flag", "args": {"flag": flag, "enabled": not enabled}}
    service = args.get("service")
    if not isinstance(service, str) or not service.strip():
        raise ValueError(f"{tool}.service must be a non-empty string")
    return {"tool": "rollback", "args": {"service": service}}


_TOKEN = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
# Strong shell markers. These are commands even inside a read-tool argument.
_SHELL_HARD = re.compile(
    r"docker\s+exec|docker\s+run|\b(?:ba)?sh\s+-c\b|&&|\|\||`|\$\(",
    re.IGNORECASE,
)
# Action arguments are executed. A pipeline or a stacked command is a shell too.
_SHELL_ACTION = re.compile(
    r"docker\s+exec|docker\s+run|\b(?:ba)?sh\s+-c\b|&&|\|\||`|\$\(|;\s*\S|\|\s*\S",
    re.IGNORECASE,
)
_COMMAND_KEYS = {"command", "cmd", "shell", "script", "exec"}


def _shell_shaped(text, *, action):
    if not isinstance(text, str):
        return False
    pattern = _SHELL_ACTION if action else _SHELL_HARD
    return pattern.search(text) is not None


def _reject_shell(value, *, action):
    """Walk a command payload. Hypothesis evidence is data and is not walked."""
    if isinstance(value, str):
        if _shell_shaped(value, action=action):
            raise ValueError(f"shell-shaped command: {value!r}")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in _COMMAND_KEYS:
                raise ValueError("shell-shaped command")
            _reject_shell(item, action=action)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _reject_shell(item, action=action)


def _typed_arg(tool, key, value, typ):
    if typ is bool:
        if not isinstance(value, bool):
            raise ValueError(f"{tool}.{key} must be a boolean")
        return value
    if typ is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{tool}.{key} must be an integer")
        if key == "replicas" and value < 1:
            raise ValueError(f"{tool}.{key}={value} is outside the allow-list")
        return value
    if typ is str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{tool}.{key} must be a non-empty string")
        if not _TOKEN.match(value):
            raise ValueError(f"{tool}.{key}={value!r} is not an allow-listed name")
        return value
    raise ValueError(f"unsupported type for {tool}.{key}")


def _validate_args(tool, args):
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ValueError(f"{tool} args must be an object")
    _reject_shell(args, action=True)
    schema = ACTION_ARGS[tool]
    clean = {}
    for key, value in args.items():
        if key not in schema:
            raise ValueError(f"{tool} takes no argument {key!r}")
        clean[key] = _typed_arg(tool, key, value, schema[key])
    missing = [key for key in schema if key not in clean]
    if missing:
        raise ValueError(f"{tool} missing argument {missing[0]!r}")
    return clean


def _validate_rollback(rollback):
    if isinstance(rollback, str):
        if not rollback.strip():
            raise ValueError("missing rollback")
        _reject_shell(rollback, action=True)
        return rollback.strip()
    if isinstance(rollback, dict):
        tool = rollback.get("tool")
        if tool not in ACTION_TOOLS:
            raise ValueError(f"unknown tool: {tool!r}")
        return {"tool": tool, "args": _validate_args(tool, rollback.get("args") or {})}
    raise ValueError("rollback must be a procedure or one allow-listed action")


def _validate_action(action):
    if not isinstance(action, dict):
        raise ValueError("an action must be an object")
    tool = action.get("tool")
    if tool not in ACTION_TOOLS:
        raise ValueError(f"unknown tool: {tool!r}")
    effect = action.get("expected_effect")
    if not isinstance(effect, str) or not effect.strip():
        raise ValueError("missing expected effect")
    if "rollback" not in action or action.get("rollback") in (None, ""):
        raise ValueError("missing rollback")
    return {
        "tool": tool,
        "args": _validate_args(tool, action.get("args") or {}),
        "expected_effect": effect.strip(),
        "rollback": _validate_rollback(action.get("rollback")),
    }


def _validate_hypotheses(raw):
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("hypotheses must be a list")
    clean = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError("each hypothesis must be an object")
        evidence = item.get("evidence") or []
        if isinstance(evidence, str):
            evidence = [evidence]
        if not isinstance(evidence, list):
            raise ValueError("hypothesis evidence must be a list")
        rank = item.get("rank", index)
        if isinstance(rank, bool) or not isinstance(rank, int):
            raise ValueError("hypothesis rank must be an integer")
        clean.append(
            {
                "service": str(item.get("service") or ""),
                "statement": str(item.get("statement") or ""),
                "evidence": [str(piece) for piece in evidence],
                "rank": rank,
                "contradicted": bool(item.get("contradicted", False)),
            }
        )
    return clean


def _action_slots(plan):
    """Every place a write can hide. More than one rejects the whole plan."""
    slots = []
    for key in ("proposed_action", "action"):
        value = plan.get(key)
        if value:
            slots.append(value)
    extra = plan.get("actions")
    if isinstance(extra, list):
        slots.extend(item for item in extra if item)
    elif extra:
        slots.append(extra)
    for step in plan.get("steps") or []:
        if isinstance(step, dict) and step.get("tool") in ACTION_TOOLS:
            slots.append(step)
    return slots


def _read_steps(plan):
    steps = []
    raw = plan.get("steps") or []
    if not isinstance(raw, list):
        raise ValueError("steps must be a list")
    for step in raw:
        if not isinstance(step, dict):
            raise ValueError("each step must be an object")
        tool = step.get("tool")
        if tool in ACTION_TOOLS:
            continue
        if tool not in READ_TOOLS:
            raise ValueError(f"unknown tool: {tool!r}")
        args = step.get("args") or {}
        if not isinstance(args, dict):
            raise ValueError(f"{tool} args must be an object")
        _reject_shell(args, action=False)
        steps.append({"tool": tool, "args": dict(args)})
    return steps


def validate_plan(plan):
    """Return a cleaned plan, or raise ``ValueError`` and run nothing.

    A cleaned plan has ``stage``, ``hypotheses`` (each with its evidence),
    read-only ``steps``, and at most one ``proposed_action``.
    """
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except json.JSONDecodeError as exc:
            raise ValueError("a plan must be JSON") from exc
    if not isinstance(plan, dict):
        raise ValueError(f"a plan must be an object, got {type(plan).__name__}")

    stage = plan.get("stage") or "scope"
    if stage not in PLAN_STAGES:
        raise ValueError(f"unknown stage: {stage!r}")

    slots = _action_slots(plan)
    if len(slots) > 1:
        raise ValueError("a plan may propose only one action")

    proposed = _validate_action(slots[0]) if slots else None
    return {
        "stage": stage,
        "hypotheses": _validate_hypotheses(plan.get("hypotheses")),
        "steps": _read_steps(plan),
        "proposed_action": proposed,
    }


def format_plan(plan) -> str:
    """Render the plan the engineer is supposed to be able to see."""
    stage = (plan or {}).get("stage") or "scope"
    progress = " → ".join(f"[{name}]" if name == stage else name for name in PLAN_STAGES)
    lines = [progress]
    for hypothesis in (plan or {}).get("hypotheses") or []:
        flag = " contradicted" if hypothesis.get("contradicted") else ""
        lines.append(
            f"  H{hypothesis.get('rank')}: [{hypothesis.get('service')}] "
            f"{hypothesis.get('statement')}{flag}"
        )
        for piece in hypothesis.get("evidence") or []:
            lines.append(f"    evidence: {piece}")
    for step in (plan or {}).get("steps") or []:
        lines.append(f"  step: {step.get('tool')} {step.get('args')}")
    action = (plan or {}).get("proposed_action")
    if action:
        lines.append(
            f"  action: {action.get('tool')} {action.get('args')} "
            f"-> {action.get('expected_effect')}"
        )
        lines.append(f"  rollback: {action.get('rollback')}")
    return "\n".join(lines)
