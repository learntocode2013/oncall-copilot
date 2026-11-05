"""One JSON object per graph run, with the path and the meter on ``meta``.

Each incident node appends its name to ``graph_path``. ``operator.add`` keeps
every visit, including a second pass through the same node. ``Solo.run`` and
``Team.run`` copy that list onto the trace after the meter block closes, so
the line has both the nodes that ran and the calls those nodes spent.

``meta.meter`` is this ``meter`` block: calls, tokens, the largest prompt,
wall-clock seconds, the per-agent split, and the rows. A resume is its own
block, so its meter covers the resume. ``graph_path`` is the whole thread,
because the checkpoint still holds the earlier names.

The trace is not a ``RunRecord``. The harness still scores the outcome. This
object is the path and the cost of one invoke.
"""

from __future__ import annotations

import copy
import json

from langgraph.types import Command

from .meter import by_agent


class TraceError(ValueError):
    """A trace line that cannot be stored or read back."""


def stamp(name, update):
    """Append ``name`` to a node return. A node that returns nothing still counts."""
    mark = [name]
    if update is None:
        return {"graph_path": mark}
    if isinstance(update, Command):
        # Command is frozen, so the path has to ride on a new one.
        raw = update.update if isinstance(update.update, dict) else {}
        payload = dict(raw)
        payload["graph_path"] = mark + list(payload.get("graph_path") or [])
        return Command(
            graph=update.graph,
            update=payload,
            resume=update.resume,
            goto=update.goto,
        )
    if not isinstance(update, dict):
        return update
    payload = dict(update)
    payload["graph_path"] = mark + list(payload.pop("graph_path", None) or [])
    return payload


def tracked(name, fn):
    """Run ``fn``, then record ``name`` on ``graph_path``."""

    def node(state):
        return stamp(name, fn(state))

    node.__name__ = name
    node.__doc__ = getattr(fn, "__doc__", None)
    return node


def add_tracked(graph, name, fn):
    """Register ``fn`` under ``name`` and append that name each time it finishes."""
    graph.add_node(name, tracked(name, fn))


def _copy_rows(rows) -> list[dict]:
    copied = []
    for row in rows or []:
        item = dict(row)
        item["tools_invoked"] = list(row.get("tools_invoked") or [])
        item["tools_available"] = list(row.get("tools_available") or [])
        copied.append(item)
    return copied


def meter_snapshot(measured=None, rows=None) -> dict:
    """The meter's figures as plain JSON.

    With a ``meter``, ``seconds`` is the wall clock of that block. Without one,
    the rows are rolled up and ``seconds`` stays ``None`` — summing call time
    is not the same number.
    """
    if measured is not None:
        return {
            "label": measured.label,
            "calls": int(measured.calls),
            "tokens": int(measured.tokens),
            "largest_prompt": int(measured.biggest),
            "seconds": measured.seconds,
            "by_agent": copy.deepcopy(measured.by_agent),
            "rows": _copy_rows(measured.rows),
        }
    copied = _copy_rows(rows)
    prompts = [int(row.get("input_tokens") or 0) for row in copied]
    tokens = sum(
        int(row.get("input_tokens") or 0) + int(row.get("output_tokens") or 0) for row in copied
    )
    return {
        "label": "",
        "calls": len(copied),
        "tokens": tokens,
        "largest_prompt": max(prompts, default=0),
        "seconds": None,
        "by_agent": by_agent(copied),
        "rows": copied,
    }


def _interrupted(final) -> bool:
    if not isinstance(final, dict):
        return False
    return bool(final.get("__interrupt__"))


def build_trace(final, *, topology, measured=None, incident_id=None) -> dict:
    """The record ``run`` keeps. Meter numbers live under ``meta``, not beside the path."""
    state = final if isinstance(final, dict) else {}
    found = state.get("incident_id") or incident_id or ""
    rows = None if measured is not None else state.get("meter_rows")
    snapshot = meter_snapshot(measured, rows)
    return {
        "incident_id": found,
        "topology": topology,
        "status": state.get("status") or "",
        "route": state.get("route") or "",
        "tool_calls": int(state.get("tool_calls") or 0),
        "proposed_action": state.get("proposed_action"),
        "verification": state.get("verification"),
        "meta": {
            "graph_path": [str(name) for name in (state.get("graph_path") or [])],
            "latency_s": None if measured is None else measured.seconds,
            "interrupted": _interrupted(state),
            "meter": snapshot,
        },
    }


def attach_trace(measured, final, *, topology, incident_id=None) -> dict:
    """Build the trace for a finished meter block and keep it on ``measured.trace``."""
    record = build_trace(final, topology=topology, measured=measured, incident_id=incident_id)
    measured.trace = record
    return record


def write_jsonl(path, records):
    """One trace per line. A later ``read_jsonl`` returns the same objects."""
    lines = (json.dumps(record, ensure_ascii=False) + "\n" for record in records)
    with open(path, "w", encoding="utf-8") as handle:
        handle.writelines(lines)


def read_jsonl(path):
    out = []
    with open(path, encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise TraceError(f"line {lineno} is not valid JSON: {exc}") from exc
    return out
