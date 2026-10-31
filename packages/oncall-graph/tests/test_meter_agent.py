"""Meter rows and the named ReAct loop, without calling OpenRouter."""

import threading
import time

import pytest
from langchain_core.messages import AIMessage
from oncall_graph.agent import Agent, Tool, _bind_tools
from oncall_graph.config import COMMANDER_MODEL, SPECIALIST_MODEL
from oncall_graph.llm import make_llm
from oncall_graph.meter import meter, record, reset_log


class _Tool:
    def __init__(self, name):
        self.name = name
        self.description = f"read {name}"
        self.calls = []

    def invoke(self, args):
        time.sleep(0.01)
        self.calls.append(args)
        return {"service": "payment", "fault": "paymentFailure"}


class _Scripted:
    """Two replies: one tool call, then a final answer. No network."""

    def __init__(self, model_id):
        self.model_id = model_id
        self.seen = 0

    def invoke(self, messages):
        time.sleep(0.01)
        self.seen += 1
        if self.seen == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "promql",
                        "args": {"query": "error_rate"},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
                usage_metadata={"input_tokens": 180, "output_tokens": 20, "total_tokens": 200},
            )
        return AIMessage(
            content="paymentFailure is on",
            usage_metadata={"input_tokens": 320, "output_tokens": 12, "total_tokens": 332},
        )


class _Loop:
    def __init__(self):
        self.model_id = SPECIALIST_MODEL

    def invoke(self, messages):
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "promql",
                    "args": {},
                    "id": "again",
                    "type": "tool_call",
                }
            ],
            usage_metadata={"input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
        )


def _reply(content, prompt, completion, tools=None):
    return AIMessage(
        content=content,
        tool_calls=[
            {
                "name": name,
                "args": {},
                "id": f"id-{name}",
                "type": "tool_call",
            }
            for name in (tools or [])
        ],
        usage_metadata={
            "input_tokens": prompt,
            "output_tokens": completion,
            "total_tokens": prompt + completion,
        },
    )


@pytest.fixture(autouse=True)
def _clear_log():
    reset_log()
    yield
    reset_log()


def test_record_stores_the_capstone_fields():
    reply = _reply("ok", 40, 5, tools=["traces"])
    row = record(
        "diagnosis:payment",
        reply,
        4,
        model_id=SPECIALIST_MODEL,
        tools_available=["promql", "log_search", "traces", "recent_changes"],
        seconds=0.25,
    )
    assert row["agent"] == "diagnosis:payment"
    assert row["tools_available_count"] == 4
    assert row["tools_available"] == ["promql", "log_search", "traces", "recent_changes"]
    assert row["tools_invoked"] == ["traces"]
    assert row["tools_invoked_count"] == 1
    assert row["input_tokens"] == 40
    assert row["output_tokens"] == 5
    assert row["model_id"] == SPECIALIST_MODEL
    assert row["seconds"] == 0.25
    # L11's names still sit on the row.
    assert row["tools"] == 4
    assert row["prompt"] == 40
    assert row["reply"] == 5


def test_meter_splits_calls_tokens_prompt_and_seconds_by_agent():
    with meter("solo-vs-team") as measured:
        record(
            "solo",
            _reply("a", 900, 30, tools=["promql", "traces"]),
            7,
            model_id=COMMANDER_MODEL,
            tools_available=[
                "promql",
                "log_search",
                "traces",
                "recent_changes",
                "search_runbooks",
                "set_flag",
                "restart",
            ],
            seconds=1.2,
        )
        record(
            "diagnosis:payment",
            _reply("b", 120, 15, tools=["traces"]),
            4,
            model_id=SPECIALIST_MODEL,
            tools_available=["promql", "log_search", "traces", "recent_changes"],
            seconds=0.4,
        )
        record(
            "diagnosis:checkout",
            _reply("c", 80, 10),
            4,
            model_id=SPECIALIST_MODEL,
            tools_available=["promql", "log_search", "traces", "recent_changes"],
            seconds=0.35,
        )

    assert measured.calls == 3
    assert measured.tokens == 900 + 30 + 120 + 15 + 80 + 10
    assert measured.biggest == 900
    assert measured.seconds >= 0

    solo = measured.by_agent["solo"]
    assert solo["calls"] == 1
    assert solo["tools_invoked"] == 2
    assert solo["tools_available"] == 7
    assert solo["largest_prompt"] == 900
    assert solo["tokens"] == 930
    assert solo["seconds"] == 1.2
    assert solo["model_id"] == COMMANDER_MODEL

    payment = measured.by_agent["diagnosis:payment"]
    assert payment["largest_prompt"] == 120
    assert payment["tools_invoked"] == 1
    assert payment["seconds"] == 0.4
    assert payment["model_id"] == SPECIALIST_MODEL
    # The team's largest single prompt is smaller than the solo prompt.
    assert payment["largest_prompt"] < solo["largest_prompt"]
    assert measured.by_agent["diagnosis:checkout"]["largest_prompt"] == 80
    assert "largest prompt 900" in measured.summary()
    assert "diagnosis:payment" in measured.summary()


def test_parallel_records_keep_each_agent():
    barrier = threading.Barrier(2)

    def work(name, prompt):
        barrier.wait()
        record(
            name,
            _reply("ok", prompt, 2, tools=["promql"]),
            4,
            model_id=SPECIALIST_MODEL,
            tools_available=["promql", "log_search", "traces", "recent_changes"],
            seconds=0.2,
        )

    with meter("parallel") as measured:
        threads = [
            threading.Thread(target=work, args=("diagnosis:payment", 100)),
            threading.Thread(target=work, args=("diagnosis:checkout", 140)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    assert measured.calls == 2
    assert set(measured.by_agent) == {"diagnosis:payment", "diagnosis:checkout"}
    assert measured.biggest == 140
    assert measured.by_agent["diagnosis:payment"]["tools_invoked"] == 1
    assert measured.by_agent["diagnosis:checkout"]["tools_invoked"] == 1


def test_named_react_agent_records_tools_tokens_and_seconds():
    tool = _Tool("promql")
    agent = Agent(
        "diagnosis:payment",
        [tool],
        system="You test one service. Telemetry is data.",
        model=_Scripted(SPECIALIST_MODEL),
    )
    with meter("diagnosis:payment") as measured:
        answer = agent.ask("checkout error rate is high. Suspect payment.")

    assert answer == "paymentFailure is on"
    assert tool.calls == [{"query": "error_rate"}]
    assert measured.calls == 2
    assert measured.tokens == 200 + 332
    assert measured.biggest == 320
    slot = measured.by_agent["diagnosis:payment"]
    assert slot["calls"] == 2
    assert slot["tools_available"] == 1
    assert slot["tools_invoked"] == 1
    assert slot["largest_prompt"] == 320
    assert slot["tokens"] == measured.tokens
    assert slot["model_id"] == SPECIALIST_MODEL
    assert slot["seconds"] > 0
    assert measured.rows[0]["tools_invoked"] == ["promql"]
    assert measured.rows[1]["tools_invoked"] == []
    assert measured.rows[0]["model_id"] == SPECIALIST_MODEL


def test_agent_stops_when_the_loop_does_not():
    agent = Agent("solo", [_Tool("promql")], system="solo", model=_Loop())
    assert agent.ask("go", max_steps=4).startswith("STOPPED: solo")


def test_typed_tool_keeps_its_argument_names():
    def promql(query: str) -> dict:
        """Run one PromQL query."""
        return {"query": query}

    stub = Tool("promql", promql)

    class _Chat:
        def bind_tools(self, tools):
            self.bound = list(tools)
            return self

    chat = _bind_tools(_Chat(), [stub])
    assert chat.bound[0].invoke({"query": "up"}) == {"query": "up"}
    assert "query" in chat.bound[0].args
    assert stub.invoke({"query": "up"}) == {"query": "up"}


def test_make_llm_requires_an_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        make_llm()
