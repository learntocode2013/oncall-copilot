"""L11's meter, extended with the fields the capstone comparison needs.

Every model call appends one row to ``LOG``. ``meter`` wraps a run — solo or
team — and reports calls, total tokens, the largest prompt, and seconds, plus
the same numbers per agent. The largest prompt is the figure that separates
one agent from a team: it is the most any single agent had to read at once.

A row holds:

* ``agent`` — who called the model (``solo``, ``diagnosis:payment``, ...)
* ``tools_available`` — names that agent was allowed to choose from
* ``tools_invoked`` — names it actually asked to run on that call
* ``input_tokens`` / ``output_tokens``
* ``model_id`` — the OpenRouter id, so the two designs stay comparable
* ``seconds`` — that call, including the tool round that followed it
"""

from __future__ import annotations

import threading
import time

LOG: list[dict] = []
VERBOSE = True
_LOCK = threading.Lock()


def say(*args):
    """Print a trace line. ``VERBOSE = False`` silences the agents, not the meter."""
    if VERBOSE:
        print(*args)


def reset_log():
    """Drop every row. Tests and a fresh incident start here."""
    with _LOCK:
        LOG.clear()


def _usage(reply) -> dict:
    raw = getattr(reply, "usage_metadata", None) or {}
    if isinstance(raw, dict):
        return raw
    return {
        "input_tokens": getattr(raw, "input_tokens", 0) or 0,
        "output_tokens": getattr(raw, "output_tokens", 0) or 0,
    }


def _invoked_names(reply, tools_invoked) -> list[str]:
    if tools_invoked is not None:
        return [str(name) for name in tools_invoked]
    calls = getattr(reply, "tool_calls", None) or []
    names = []
    for call in calls:
        if isinstance(call, dict):
            names.append(str(call.get("name") or ""))
        else:
            names.append(str(getattr(call, "name", "") or ""))
    return names


def _model_id_from(reply, model_id):
    if model_id:
        return str(model_id)
    meta = getattr(reply, "response_metadata", None) or {}
    if isinstance(meta, dict):
        found = meta.get("model_name") or meta.get("model") or ""
        if found:
            return str(found)
    return ""


def record(
    agent, reply, n_tools, model_id=None, tools_available=None, tools_invoked=None, seconds=None
):
    """Append one model call. ``n_tools`` is L11's positional tool count.

    Pass ``tools_available`` as the names the agent could call, and
    ``tools_invoked`` as the names this reply asked for. Both default from
    ``n_tools`` and from ``reply.tool_calls`` so ``record(name, reply, n)``
    still matches the lab.
    """
    usage = _usage(reply)
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    if tools_available is None:
        available = []
        available_count = int(n_tools or 0)
    else:
        available = [str(name) for name in tools_available]
        available_count = len(available)
    invoked = _invoked_names(reply, tools_invoked)
    row = {
        "agent": agent,
        "tools_available": available,
        "tools_available_count": available_count,
        "tools_invoked": invoked,
        "tools_invoked_count": len(invoked),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "model_id": _model_id_from(reply, model_id),
        "seconds": round(float(seconds or 0), 3),
        # L11 names, kept so a row is still (who, tools, prompt, reply).
        "tools": available_count,
        "prompt": input_tokens,
        "reply": output_tokens,
    }
    with _LOCK:
        LOG.append(row)
    return row


def by_agent(rows) -> dict:
    """Calls, tool counts, largest prompt, tokens, and seconds for each agent."""
    out: dict[str, dict] = {}
    for row in rows:
        name = row.get("agent") or ""
        slot = out.get(name)
        if slot is None:
            slot = {
                "calls": 0,
                "tools_available": 0,
                "tools_invoked": 0,
                "largest_prompt": 0,
                "tokens": 0,
                "seconds": 0.0,
                "model_id": row.get("model_id") or "",
            }
            out[name] = slot
        slot["calls"] += 1
        slot["tools_available"] = max(
            slot["tools_available"], int(row.get("tools_available_count") or 0)
        )
        slot["tools_invoked"] += int(row.get("tools_invoked_count") or 0)
        prompt = int(row.get("input_tokens") or 0)
        slot["largest_prompt"] = max(slot["largest_prompt"], prompt)
        slot["tokens"] += prompt + int(row.get("output_tokens") or 0)
        slot["seconds"] += float(row.get("seconds") or 0)
        if row.get("model_id"):
            slot["model_id"] = row["model_id"]
    for slot in out.values():
        slot["seconds"] = round(slot["seconds"], 3)
    return out


class meter:
    """``with meter("label") as m`` then ``m.calls``, ``m.tokens``, ``m.biggest``, ``m.seconds``.

    ``m.by_agent`` splits those figures by the ``agent`` on each row. ``m.seconds``
    is wall-clock for the block. Per-agent seconds sum that agent's own calls,
    so overlapping diagnosis runs each keep their own time.
    """

    def __init__(self, label):
        self.label = label
        self.rows: list[dict] = []
        self.seconds = 0.0
        self.calls = 0
        self.tokens = 0
        self.biggest = 0
        self.by_agent: dict[str, dict] = {}

    def __enter__(self):
        with _LOCK:
            self.start = len(LOG)
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.seconds = round(time.perf_counter() - self.t0, 3)
        with _LOCK:
            self.rows = list(LOG[self.start :])
        self.calls = len(self.rows)
        self.tokens = sum(int(r["input_tokens"]) + int(r["output_tokens"]) for r in self.rows)
        self.biggest = max((int(r["input_tokens"]) for r in self.rows), default=0)
        self.by_agent = by_agent(self.rows)
        print(self.summary())
        return False

    def summary(self) -> str:
        shown = round(self.seconds, 1)
        lines = [
            (
                f"[{self.label}] {self.calls} calls, {self.tokens:,} tokens, "
                f"largest prompt {self.biggest:,} tokens, {shown}s"
            )
        ]
        for name, row in self.by_agent.items():
            model = f", model {row['model_id']}" if row.get("model_id") else ""
            lines.append(
                f"  {name}: {row['calls']} calls, {row['tools_invoked']} tools invoked, "
                f"largest prompt {row['largest_prompt']:,} tokens, "
                f"{row['tokens']:,} tokens, {row['seconds']}s{model}"
            )
        return "\n".join(lines)
