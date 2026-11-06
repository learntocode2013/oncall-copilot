# Every rejected plan raises ValueError. Callers catch that one type.
# ruff: noqa: TRY004
"""The visible incident plan, and the checks that keep it executable.

``validate_plan`` checks the visible plan: stage and read steps. Hypotheses and
the one write are validated beside it. A shell-shaped command, an unknown tool,
or a write with no expected effect is rejected. Nothing in a rejected plan runs.
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
    # "recent_changes",
    "search_runbooks",
    "incident_record",
    "status_draft",
)
ACTION_TOOLS = ("set_flag", "restart", "scale", "rollback")
KNOWN_TOOLS = READ_TOOLS + ACTION_TOOLS

#: The tool that restores a flag to an earlier variant. DELIBERATELY NOT IN
#: `ACTION_TOOLS`: a model may not propose a re-arm, and `validate_action`
#: rejects it as an unknown action. Only `monitor.code_rollback` names it, which
#: is the same split `mcp-ops-actions` enforces server-side -- one tool that can
#: only clear a fault, one that can restore one, so "undo" never reads as "fix".
ROLLBACK_FLAG_TOOL = "rollback_flag"
#: Everything `Shop.apply` will run: the proposable actions plus the code-only
#: inverse above.
WRITE_TOOLS = ACTION_TOOLS + (ROLLBACK_FLAG_TOOL,)


class Variant(str):
    """A flagd variant NAME, which is not an allow-listed identifier.

    `set_flag` used to take `enabled: bool`, and a boolean needs no pattern. The
    real flags are not two-valued: the corpus names `off`, `on`, `100%`, `1000x`
    and `10sec`, and `_TOKEN` -- which every other string argument is checked
    against -- rejects three of those five for starting with a digit or carrying
    a `%`. Validating a variant as a token would therefore accept every fix and
    refuse every rollback, which is a failure that only appears while undoing.

    So variants get `_VARIANT`: wider than a token, still no whitespace and none
    of the characters a shell reads. The authoritative list lives in the runbook
    corpus, which this package may not import, and `mcp-ops-actions` checks
    against it -- this is the shape check that runs before the wire.
    """


# Typed arguments the executor will accept. Totals and shell strings are not here.
ACTION_ARGS = {
    "set_flag": {"flag": str, "variant": Variant},
    "restart": {"service": str},
    "scale": {"service": str, "replicas": int},
    "rollback": {"service": str},
}


def default_rollback(action, args):
    """The procedure for the inverse. Code derives the shop call from the action itself."""
    if action not in ACTION_TOOLS:
        raise ValueError(f"unknown action: {action!r}")
    args = dict(args or {})
    if action == "set_flag":
        # THE INVERSE IS NO LONGER COMPUTABLE HERE. With a boolean it was `not
        # enabled`; with a variant there is no `not "100%"`, and the variant that
        # re-arms a fault is named by the runbook -- which this package may not
        # read. So this returns the procedure, and `monitor.code_rollback` builds
        # the actual call from the `previous` variant the forward write reported.
        # That is exact where negation was only ever an assumption.
        flag = args.get("flag")
        variant = args.get("variant")
        if not isinstance(flag, str) or not flag.strip():
            raise ValueError("set_flag.flag must be a non-empty string")
        if not isinstance(variant, str) or not variant.strip():
            raise ValueError("set_flag.variant must be a non-empty string")
        return f"rollback_flag {flag} to its previous variant"
    service = args.get("service")
    if not isinstance(service, str) or not service.strip():
        raise ValueError(f"{action}.service must be a non-empty string")
    return f"rollback {service}"


_TOKEN = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
# Variant names: may start with a digit and may carry `%`, `.` or `-`. No
# whitespace and nothing a shell reads, so `_reject_shell` still has nothing
# left to find here. See `Variant`.
_VARIANT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.%-]*$")
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
    if typ is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{tool}.{key} must be an integer")
        if key == "replicas" and value < 1:
            raise ValueError(f"{tool}.{key}={value} is outside the allow-list")
        return value
    if typ is Variant:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{tool}.{key} must be a non-empty string")
        if not _VARIANT.match(value):
            raise ValueError(f"{tool}.{key}={value!r} is not a variant name")
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
    if not isinstance(rollback, str) or not rollback.strip():
        raise ValueError("rollback must be a procedure string")
    _reject_shell(rollback, action=True)
    return rollback.strip()


def validate_action(action):
    """One write. The command key is ``action``. Rollback is a procedure string."""
    if not isinstance(action, dict):
        raise ValueError("an action must be an object")
    if "tool" in action:
        raise ValueError("the command key is action")
    name = action.get("action")
    if name not in ACTION_TOOLS:
        raise ValueError(f"unknown action: {name!r}")
    effect = action.get("expected_effect")
    if not isinstance(effect, str) or not effect.strip():
        raise ValueError("missing expected effect")
    if "rollback" not in action or action.get("rollback") in (None, ""):
        raise ValueError("missing rollback")
    return {
        "action": name,
        "args": _validate_args(name, action.get("args") or {}),
        "expected_effect": effect.strip(),
        "rollback": _validate_rollback(action.get("rollback")),
    }


def validate_hypotheses(raw):
    """Contract hypotheses. Evidence is references, not log lines."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("hypotheses must be a list")
    clean = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each hypothesis must be an object")
        service = item.get("service")
        statement = item.get("statement")
        refs = item.get("evidence_refs")
        confidence = item.get("confidence")
        if not isinstance(service, str) or not service.strip():
            raise ValueError("hypothesis service must be a non-empty string")
        if not isinstance(statement, str) or not statement.strip():
            raise ValueError("hypothesis statement must be a non-empty string")
        if not isinstance(refs, list) or not all(isinstance(ref, str) for ref in refs):
            raise ValueError("hypothesis evidence_refs must be a list of strings")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError("hypothesis confidence must be a number")
        clean.append(
            {
                "service": service.strip(),
                "statement": statement.strip(),
                "evidence_refs": list(refs),
                "confidence": float(confidence),
            }
        )
    return clean


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
            raise ValueError("writes do not belong in the plan")
        if tool not in READ_TOOLS:
            raise ValueError(f"unknown tool: {tool!r}")
        args = step.get("args") or {}
        if not isinstance(args, dict):
            raise ValueError(f"{tool} args must be an object")
        _reject_shell(args, action=False)
        steps.append({"tool": tool, "args": dict(args)})
    return steps


def validate_plan(plan):
    """Return ``{stage, steps}``, or raise ``ValueError`` and run nothing."""
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except json.JSONDecodeError as exc:
            raise ValueError("a plan must be JSON") from exc
    if not isinstance(plan, dict):
        raise ValueError(f"a plan must be an object, got {type(plan).__name__}")
    if any(key in plan for key in ("hypotheses", "proposed_action", "action", "actions")):
        raise ValueError("hypotheses and the action belong beside the plan")

    stage = plan.get("stage") or "scope"
    if stage not in PLAN_STAGES:
        raise ValueError(f"unknown stage: {stage!r}")
    return {"stage": stage, "steps": _read_steps(plan)}


def format_plan(plan, hypotheses=None, proposed_action=None) -> str:
    """Render the stage, the remaining reads, and the sibling explanations and write."""
    stage = (plan or {}).get("stage") or "scope"
    progress = " → ".join(f"[{name}]" if name == stage else name for name in PLAN_STAGES)
    lines = [progress]
    for hypothesis in hypotheses or []:
        lines.append(
            f"  [{hypothesis.get('service')}] {hypothesis.get('statement')} "
            f"({hypothesis.get('confidence')})"
        )
        for ref in hypothesis.get("evidence_refs") or []:
            lines.append(f"    evidence: {ref}")
    for step in (plan or {}).get("steps") or []:
        lines.append(f"  step: {step.get('tool')} {step.get('args')}")
    if proposed_action:
        lines.append(
            f"  action: {proposed_action.get('action')} {proposed_action.get('args')} "
            f"-> {proposed_action.get('expected_effect')}"
        )
        lines.append(f"  rollback: {proposed_action.get('rollback')}")
    return "\n".join(lines)
