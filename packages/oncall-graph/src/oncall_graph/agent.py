"""L11's named ReAct agent: llm → action → llm, until the model stops calling tools.

The only difference between specialists is the name, the prompt, and the tools
they are given. ``record`` runs inside ``call_llm``, so the meter can attribute
calls, tool counts, prompt size, tokens, and seconds to that name.
"""

import operator
import time
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph

from .config import SPECIALIST_MODEL
from .llm import make_llm
from .meter import record, say


class AgentState(TypedDict):
    messages: Annotated[list, operator.add]


class Tool:
    """A callable the specialist may use. ``fn`` keeps its real parameter names.

    Later MCP tools replace this. ``Agent`` only needs ``name`` and ``invoke``.
    """

    def __init__(self, name, fn, description=None):
        self.name = name
        self.fn = fn
        self.description = description or (fn.__doc__ or name)

    def invoke(self, args):
        return self.fn(**(args or {}))


def _as_langchain_tool(tool):
    """LangChain tool, or a ``Tool`` whose ``fn`` has a real signature."""
    if isinstance(tool, BaseTool):
        return tool
    fn = getattr(tool, "fn", None)
    if not callable(fn):
        raise TypeError(
            f"{getattr(tool, 'name', tool)!r} must be a LangChain tool or a Tool "
            "whose fn has typed parameters"
        )
    description = getattr(tool, "description", "") or (fn.__doc__ or tool.name)
    return StructuredTool.from_function(fn, name=tool.name, description=description)


def _bind_tools(chat, tools):
    if not tools:
        return chat
    return chat.bind_tools([_as_langchain_tool(tool) for tool in tools])


class Agent:
    """One specialist, or the solo baseline. ``name`` is what the meter stores.

    ``tool_budget`` caps how many tools this loop may run. The solo graph sets
    it to the calls this incident still has left.
    """

    def __init__(self, name, tools, system, model=None, tool_budget=None):
        self.name = name
        self.system = system or ""
        self.tools = {t.name: t for t in tools}
        self.tool_names = list(self.tools)
        # None means no cap. 0 is a real cap: the model may answer, but no tool runs.
        self.tool_budget = tool_budget
        self.tool_calls = 0
        self._stop = None
        self._pending_row = None
        if model is not None and hasattr(model, "invoke"):
            self.model_id = (
                getattr(model, "model_id", None) or getattr(model, "model_name", None) or ""
            )
            self.model = model
        else:
            self.model_id = model or SPECIALIST_MODEL
            self.model = _bind_tools(make_llm(self.model_id), tools)

        graph = StateGraph(AgentState)
        graph.add_node("llm", self.call_llm)
        graph.add_node("action", self.take_action)
        graph.add_edge(START, "llm")
        graph.add_conditional_edges("llm", self.exists_action, {True: "action", False: END})
        graph.add_conditional_edges("action", self.after_action, {"llm": "llm", "end": END})
        self.graph = graph.compile()

    def call_llm(self, state):
        if self._stop:
            return {"messages": [AIMessage(content=self._stop)]}
        started = time.perf_counter()
        reply = self.model.invoke([SystemMessage(content=self.system)] + state["messages"])
        invoked = []
        for call in reply.tool_calls or []:
            name = call.get("name") if isinstance(call, dict) else getattr(call, "name", "")
            invoked.append(str(name or ""))
        self._pending_row = record(
            self.name,
            reply,
            len(self.tools),
            model_id=self.model_id,
            tools_available=self.tool_names,
            tools_invoked=invoked,
            seconds=time.perf_counter() - started,
        )
        return {"messages": [reply]}

    def exists_action(self, state):
        last = state["messages"][-1]
        return len(getattr(last, "tool_calls", None) or []) > 0

    def after_action(self, state):
        """Leave the loop once the tool budget is spent. Otherwise ask the model again."""
        if self._stop:
            return "end"
        return "llm"

    def take_action(self, state):
        """Run every tool the model asked for. A missing tool is an error string.

        A call past ``tool_budget`` is not run and does not count. The row lists
        the tools that ran.
        """
        started = time.perf_counter()
        results = []
        invoked = []
        for call in state["messages"][-1].tool_calls:
            name = call["name"]
            args = call["args"]
            if self.tool_budget is not None and self.tool_calls >= self.tool_budget:
                self._stop = f"STOPPED: {self.name} hit its tool budget of {self.tool_budget}."
                results.append(ToolMessage(tool_call_id=call["id"], name=name, content=self._stop))
                continue
            invoked.append(name)
            say(f"      {self.name} calls {name}({args})")
            fn = self.tools.get(name)
            if fn is None:
                result = f"{self.name} has no tool called {name}"
            else:
                result = fn.invoke(args)
            self.tool_calls += 1
            if self.tool_budget is not None and self.tool_calls >= self.tool_budget:
                self._stop = f"STOPPED: {self.name} hit its tool budget of {self.tool_budget}."
            results.append(ToolMessage(tool_call_id=call["id"], name=name, content=str(result)))
        if self._pending_row is not None:
            self._pending_row["seconds"] = round(
                self._pending_row["seconds"] + (time.perf_counter() - started), 3
            )
            self._pending_row["tools_invoked"] = list(invoked)
            self._pending_row["tools_invoked_count"] = len(invoked)
        return {"messages": results}

    def ask(self, text, max_steps=24):
        """Run the loop on one brief and return the final reply text."""
        self.tool_calls = 0
        self._stop = None
        self._pending_row = None
        try:
            out = self.graph.invoke(
                {"messages": [HumanMessage(content=text)]},
                {"recursion_limit": max_steps},
            )
        except GraphRecursionError:
            return f"STOPPED: {self.name} hit its step limit without finishing."
        if self._stop:
            return self._stop
        content = out["messages"][-1].content
        if isinstance(content, str):
            return content
        return str(content)
