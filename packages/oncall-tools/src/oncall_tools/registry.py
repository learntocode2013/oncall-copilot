"""`load_tools(transport)` -- the MCP client, and the registry it produces.

This is the only way the graph reaches a tool. `oncall_graph` may import
`oncall_tools` but NOT `oncall_knowledge` (enforced by `make guards`), so there
is no path from a topology to `Retriever` that bypasses MCP. That matters for
E1: if one topology retrieved through MCP and another imported directly, the
comparison between them would differ in retrieval as well as in orchestration.

WHAT A TOOL LOOKS LIKE HERE. `ToolSpec` is framework-neutral: a name, a
description, the server's own JSON Schema, a write flag and a callable. It is
deliberately not a LangChain tool -- binding P3's package to the agent framework
would mean the tool layer could not be reused or tested without it. The
adaptation to LangChain belongs in `oncall_graph.agent._as_langchain_tool`,
which already has the branch for it.

WRITE MARKING COMES OFF THE WIRE, AND FAILS CLOSED. Each server declares
`ToolAnnotations(read_only_hint=...)` per tool, and that declaration is the
single source of truth. Anything that does not say `read_only_hint is True` --
including a tool with no annotations at all -- is treated as a WRITE. A
hand-written allowlist here would be a second copy of what the servers already
say, and the copy is what drifts.

TRANSPORTS. One `mcp.client.Client` handles all three cases, because it accepts
a URL, a `StdioServerParameters`, or a server object directly:

    http      Client("http://mcp-runbooks:8102/mcp")    each server a container
    stdio     Client(StdioServerParameters(...))        spawned as a subprocess
    in-process  Client(build_server(...))               tests; no port, no fork

So `build_graph(tools=load_tools())` is identical in all three and the graph
never knows which is live.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import Future
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Self

from mcp import StdioServerParameters
from mcp.client import Client
from oncall_contracts.config import Transport

DEFAULT_TRANSPORT: Transport = "stdio"

#: Environment variable the Compose overlay sets. CONTRIBUTING.md documents
#: `MCP_TRANSPORT=stdio` for development and `http` for Docker.
TRANSPORT_ENV = "MCP_TRANSPORT"


@dataclass(frozen=True, slots=True)
class ServerSpec:
    """How to reach one MCP server, in either transport.

    ONLY SERVERS THAT EXIST ARE LISTED. The October plan names four --
    telemetry (8101), runbooks (8102), tickets (8103) and ops_actions (8104,
    the only write server) -- and three are unwritten. Listing an unwritten
    server would make `load_tools()` fail for everyone until all four landed,
    or, worse, make it return fewer tools than it claimed. Adding a server is
    one entry here, in the commit that adds the server.
    """

    name: str
    module: str
    port: int

    def url(self, host: str) -> str:
        return f"http://{host}:{self.port}/mcp"


SERVERS: Mapping[str, ServerSpec] = {
    "runbooks": ServerSpec("runbooks", "oncall_tools.servers.runbooks_kb", 8102),
    # The only write server. It is in the registry like any other -- what keeps
    # its tools away from a diagnosis agent is `permissions.for_role`, not
    # omission here, and not network reachability.
    "ops_actions": ServerSpec("ops_actions", "oncall_tools.servers.ops_actions", 8104),
}


class UnknownServerError(KeyError):
    """Raised for a server name that is not in SERVERS.

    Loud rather than skipped: a registry that quietly omits a server the caller
    asked for produces an agent that cannot explain why it never searched
    runbooks.
    """


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One tool, as the registry hands it to the graph.

    `schema` is the server's advertised JSON Schema for the arguments, passed
    through untouched -- the server owns its contract. `write` is derived from
    the server's own annotation, fail-closed. `invoke` is synchronous: see
    `_Bridge` for why.
    """

    name: str
    description: str
    schema: Mapping[str, Any]
    write: bool
    server: str
    invoke: Callable[..., Any]

    @property
    def read(self) -> bool:
        return not self.write


def _is_write(annotations: Any) -> bool:
    """Fail closed.

    A tool is a read ONLY if it says so. `None` annotations, a missing hint and
    an explicit `False` all mean write, so a server author who forgets to
    annotate gets their tool quarantined out of diagnosis registries rather than
    trusted into them. `runbooks_kb` annotates both of its tools.
    """
    return getattr(annotations, "read_only_hint", None) is not True


class _Bridge:
    """An event loop on a background thread, so sync callers can drive async
    MCP sessions.

    An MCP session is a long-lived async context manager: opening one per tool
    call would re-handshake (and, in stdio, re-spawn a subprocess and reload an
    80 MB model) on every call. So the sessions are opened once and each sync
    `invoke` hands a coroutine to the loop that owns them.

    The alternative was making the whole tool path async, which would have
    pushed async through `Agent.invoke` and every node in P2's graph for no gain
    this project can measure.

    ONE TASK OWNS THE SESSIONS. anyio binds a cancel scope to the task that
    entered it, so opening the sessions in one task and closing them in another
    fails with "Attempted to exit cancel scope in a different task than it was
    entered in" -- measured, not theorised. `_session` below therefore opens the
    stack, publishes the tools, waits to be told to stop, and closes the stack,
    all in a single task. Individual `call_tool`s may come from any task: the
    restriction is on entering and exiting the context, not on using it.
    """

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._serve, name="mcp-registry", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def submit(self, coro: Any) -> Future[Any]:
        """Schedule `coro` on the bridge's loop without waiting for it."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def run(self, coro: Any) -> Any:
        """Block until `coro` finishes on the bridge's loop.

        Called from the graph's thread. The exception propagates here unchanged,
        so an MCP error surfaces at the tool call site rather than as a dead
        thread.
        """
        return self.submit(coro).result()

    def call_soon(self, fn: Callable[[], Any]) -> None:
        self._loop.call_soon_threadsafe(fn)

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        self._loop.close()


class ToolRegistry:
    """What `load_tools()` returns: the tools, and the sessions behind them.

    Iterable and indexable by tool name. `reads()` and `writes()` return
    narrowed registries that share the same live sessions, which is what
    `permissions.for_role` is built on -- a narrowed registry is a different
    view of the same connections, not a second set of them.
    """

    def __init__(
        self, tools: Sequence[ToolSpec], *, close: Callable[[], None] | None = None
    ) -> None:
        self._tools = tuple(tools)
        self._close = close

    @property
    def tools(self) -> tuple[ToolSpec, ...]:
        return self._tools

    @property
    def names(self) -> list[str]:
        return [tool.name for tool in self._tools]

    def __iter__(self) -> Iterator[ToolSpec]:
        return iter(self._tools)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return any(tool.name == name for tool in self._tools)

    def __getitem__(self, name: str) -> ToolSpec:
        for tool in self._tools:
            if tool.name == name:
                return tool
        raise KeyError(f"no tool named {name!r}; registry has {self.names}")

    def select(self, predicate: Callable[[ToolSpec], bool]) -> ToolRegistry:
        """A narrowed view over the SAME sessions, so it must not close them."""
        return ToolRegistry([t for t in self._tools if predicate(t)], close=None)

    def reads(self) -> ToolRegistry:
        return self.select(lambda tool: tool.read)

    def writes(self) -> ToolRegistry:
        return self.select(lambda tool: tool.write)

    def close(self) -> None:
        """Closes the sessions. A narrowed view has nothing to close."""
        if self._close is not None:
            self._close()
            self._close = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _client(spec: ServerSpec, transport: Transport, host: str) -> Client:
    if transport == "http":
        return Client(spec.url(host))
    return Client(
        StdioServerParameters(command="python", args=["-m", spec.module]),
    )


def _spec_from(tool: Any, server: str, call: Callable[..., Any]) -> ToolSpec:
    return ToolSpec(
        name=tool.name,
        description=tool.description or tool.name,
        schema=tool.input_schema or {},
        write=_is_write(tool.annotations),
        server=server,
        invoke=call,
    )


def load_tools(
    transport: Transport | None = None,
    *,
    servers: Sequence[str] | None = None,
    host: str = "localhost",
    connect: Callable[[ServerSpec], Client] | None = None,
) -> ToolRegistry:
    """Connect to each server and return its tools as one registry.

    `transport` defaults to $MCP_TRANSPORT, then to stdio -- so a developer who
    sets nothing gets subprocesses, and Compose sets `http` for containers.

    `connect` is the test seam: pass a factory returning a `Client` over any
    transport, and the registry is built the same way it is in production. The
    tests use `Client(build_server(semantic=False))`, which runs the real server
    in this process -- no port, no subprocess, and no torch, so the registry is
    covered by the CI test job rather than only by a container. Nothing in the
    shipped path passes it.

    The sessions stay open until `close()`. Use it as a context manager, or call
    `close()` when the graph is done; the registry owns a background thread.
    """
    transport = transport or _transport_from_env()
    open_client = connect or (lambda server: _client(server, transport, host))
    names = list(SERVERS) if servers is None else list(servers)
    unknown = [name for name in names if name not in SERVERS]
    if unknown:
        raise UnknownServerError(f"unknown server(s) {unknown}; known: {sorted(SERVERS)}")

    bridge = _Bridge()
    ready: Future[list[ToolSpec]] = Future()
    holder: dict[str, asyncio.Event] = {}

    async def session() -> None:
        """Opens the sessions, publishes the tools, waits, closes the sessions.

        All four in one task, because anyio will not let a cancel scope be
        exited from a different task than entered it.
        """
        stop = asyncio.Event()
        holder["stop"] = stop
        try:
            async with AsyncExitStack() as stack:
                specs: list[ToolSpec] = []
                for name in names:
                    server = SERVERS[name]
                    client = await stack.enter_async_context(open_client(server))
                    for tool in (await client.list_tools()).tools:
                        specs.append(_spec_from(tool, name, _caller(bridge, client, tool.name)))
                ready.set_result(specs)
                await stop.wait()
        # Broad: the caller's thread is where this is re-raised, below.
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
            else:
                raise

    task = bridge.submit(session())

    try:
        tools = ready.result()
    except BaseException:
        # A half-open stack leaks a subprocess per server, and the next attempt
        # would then fail with a port already in use rather than the real cause.
        bridge.close()
        raise

    def close() -> None:
        stop = holder.get("stop")
        if stop is not None:
            bridge.call_soon(stop.set)
            task.result(timeout=10)
        bridge.close()

    return ToolRegistry(tools, close=close)


def _caller(bridge: _Bridge, client: Client, name: str) -> Callable[..., Any]:
    """Build the sync callable for one tool.

    Accepts either a single mapping (`invoke({"query": ...})`, how the stub
    Tool is called) or keyword arguments, because the LangChain adapter on the
    other side of the seam does the latter.
    """

    def invoke(args: Mapping[str, Any] | None = None, /, **kwargs: Any) -> Any:
        payload = dict(args or {})
        payload.update(kwargs)
        result = bridge.run(client.call_tool(name, payload))
        if result.is_error:
            raise ToolCallError(name, _text_of(result))
        structured = result.structured_content
        if structured is not None:
            # A list-returning tool is wrapped under "result"; an object-
            # returning one is the mapping itself.
            return structured.get("result", structured) if "result" in structured else structured
        return _text_of(result)

    return invoke


class ToolCallError(RuntimeError):
    """An MCP tool reported an error.

    Raised rather than returned, so a failed tool cannot read to the model as a
    tool that found nothing -- the distinction the whole corpus design rests on.
    """

    def __init__(self, tool: str, detail: str) -> None:
        self.tool = tool
        self.detail = detail
        super().__init__(f"{tool} failed: {detail}")


def _text_of(result: Any) -> str:
    return "\n".join(block.text for block in (result.content or []) if getattr(block, "text", None))


def _transport_from_env() -> Transport:
    value = os.environ.get(TRANSPORT_ENV, "").strip().lower()
    if value in ("stdio", "http"):
        return value  # type: ignore[return-value]
    if value:
        raise ValueError(f"{TRANSPORT_ENV}={value!r} is not a transport; use 'stdio' or 'http'")
    return DEFAULT_TRANSPORT
