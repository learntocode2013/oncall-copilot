"""Graph path on the channel, and the meter copied onto the trace meta."""

from types import SimpleNamespace
from typing import Annotated, get_args, get_origin, get_type_hints

from langchain_core.messages import AIMessage
from langgraph.types import Command
from oncall_graph.config import SPECIALIST_MODEL
from oncall_graph.meter import reset_log
from oncall_graph.state import IncidentChannel
from oncall_graph.topologies.c1_single import Solo
from oncall_graph.trace import (
    TraceError,
    build_trace,
    read_jsonl,
    stamp,
    tracked,
    write_jsonl,
)

ALERT = "checkout error rate above 5% for five minutes"


class Scripted:
    def __init__(self, steps):
        self.steps = list(steps)
        self.model_id = SPECIALIST_MODEL
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        step = self.steps.pop(0) if self.steps else {"content": "done", "tool_calls": []}
        return AIMessage(
            content=step.get("content") or "",
            tool_calls=step.get("tool_calls") or [],
            usage_metadata={
                "input_tokens": 50 + self.calls,
                "output_tokens": 5,
                "total_tokens": 55 + self.calls,
            },
        )


def _call(name, args, call_id):
    return {
        "content": "",
        "tool_calls": [{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    }


def _text(content):
    return {"content": content, "tool_calls": []}


def _payment_steps():
    return [
        _call("promql", {"query": "checkout_error_rate"}, "c1"),
        _call("traces", {"service": "payment"}, "c2"),
        _call(
            "set_flag",
            {
                "flag": "paymentFailure",
                "enabled": False,
                "expected_effect": "checkout error rate falls below 5%",
            },
            "c3",
        ),
        _text("paymentFailure flag is on"),
    ]


def setup_function():
    reset_log()


def teardown_function():
    reset_log()


def test_graph_path_appends_each_visit():
    annotation = get_type_hints(IncidentChannel, include_extras=True)["graph_path"]
    assert get_origin(annotation) is Annotated
    reducer = get_args(annotation)[1]
    assert reducer(["triage"], ["diagnosis"]) == ["triage", "diagnosis"]
    assert reducer(["solo"], ["solo"]) == ["solo", "solo"]


def test_stamp_names_the_node_and_keeps_the_rest_of_the_update():
    def node(state):
        return {"status": state.get("status") or "RUNNING", "route": "triage"}

    assert tracked("triage", node)({}) == {
        "status": "RUNNING",
        "route": "triage",
        "graph_path": ["triage"],
    }
    assert stamp("stop", None) == {"graph_path": ["stop"]}
    command = Command(update={"route": "execute"})
    stamped = stamp("approval", command)
    assert stamped is not command
    assert stamped.update["graph_path"] == ["approval"]
    assert stamped.update["route"] == "execute"


def test_build_trace_puts_the_meter_on_meta(tmp_path):
    measured = SimpleNamespace(
        label="solo",
        calls=1,
        tokens=45,
        biggest=40,
        seconds=1.25,
        by_agent={"solo": {"calls": 1, "tokens": 45, "largest_prompt": 40, "seconds": 0.2}},
        rows=[
            {
                "agent": "solo",
                "input_tokens": 40,
                "output_tokens": 5,
                "tools_invoked": ["promql"],
                "tools_available": ["promql", "traces"],
            }
        ],
    )

    final = {
        "incident_id": "inc-1",
        "status": "RUNNING",
        "route": "approval",
        "tool_calls": 2,
        "graph_path": ["input_guard_stub", "solo", "propose_one_action"],
        "proposed_action": {"action": "set_flag", "args": {"flag": "paymentFailure"}},
        "verification": None,
        "__interrupt__": [{"value": {"command": {}}}],
    }
    record = build_trace(final, topology="c1_single", measured=measured)
    assert record["incident_id"] == "inc-1"
    assert record["topology"] == "c1_single"
    assert record["meta"]["graph_path"] == [
        "input_guard_stub",
        "solo",
        "propose_one_action",
    ]
    assert record["meta"]["latency_s"] == 1.25
    assert record["meta"]["interrupted"] is True
    assert record["meta"]["meter"]["calls"] == 1
    assert record["meta"]["meter"]["tokens"] == 45
    assert record["meta"]["meter"]["largest_prompt"] == 40
    assert record["meta"]["meter"]["seconds"] == 1.25
    assert record["meta"]["meter"]["by_agent"]["solo"]["calls"] == 1
    assert record["meta"]["meter"]["rows"][0]["tools_invoked"] == ["promql"]
    assert "__interrupt__" not in record

    path = tmp_path / "traces.jsonl"
    write_jsonl(path, [record])
    assert read_jsonl(path) == [record]
    path.write_text("{not json}\n", encoding="utf-8")
    try:
        read_jsonl(path)
    except TraceError as exc:
        assert "line 1" in str(exc)
    else:
        raise AssertionError("bad json should raise TraceError")


def test_solo_run_records_the_path_and_the_meter():
    solo = Solo(model=Scripted(_payment_steps()), tool_budget=10)
    paused, measured = solo.run(ALERT, incident_id="trace-1")

    assert paused["graph_path"] == ["input_guard_stub", "solo", "propose_one_action"]
    trace = measured.trace
    assert trace is solo.last_trace
    assert trace["incident_id"] == "trace-1"
    assert trace["topology"] == "c1_single"
    assert trace["status"] == "RUNNING"
    assert trace["tool_calls"] == paused["tool_calls"]
    assert trace["meta"]["graph_path"] == paused["graph_path"]
    assert trace["meta"]["interrupted"] is True
    assert trace["meta"]["meter"]["label"] == "solo"
    assert trace["meta"]["meter"]["calls"] == measured.calls == 4
    assert trace["meta"]["meter"]["tokens"] == measured.tokens
    assert trace["meta"]["meter"]["largest_prompt"] == measured.biggest
    assert trace["meta"]["meter"]["seconds"] == measured.seconds == trace["meta"]["latency_s"]
    assert trace["meta"]["meter"]["by_agent"]["solo"]["calls"] == 4
    assert trace["meta"]["meter"]["by_agent"]["solo"]["tools_invoked"] == 3
    assert [row["agent"] for row in trace["meta"]["meter"]["rows"]] == ["solo"] * 4
    assert "approval" not in trace["meta"]["graph_path"]

    done, resumed = solo.run(resume={"decision": "approve"}, incident_id="trace-1")
    assert done["graph_path"] == [
        "input_guard_stub",
        "solo",
        "propose_one_action",
        "approval",
        "execute_action",
        "verify",
    ]
    finished = resumed.trace
    assert finished["status"] == "RECOVERED"
    assert finished["meta"]["graph_path"] == done["graph_path"]
    assert finished["meta"]["interrupted"] is False
    assert finished["meta"]["meter"]["calls"] == 0
    assert finished["meta"]["meter"]["rows"] == []
    assert finished["verification"]["recovered"] is True
