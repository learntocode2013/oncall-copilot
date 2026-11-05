"""`load_tools()` against the real runbooks server, running in this process.

Every test here connects a real MCP client to a real MCP server over the SDK's
in-process transport: no port, no subprocess, and `semantic=False` so no torch.
That means the registry is covered by the CI `test` job rather than only by the
container check, and the thing under test is the actual wire protocol rather
than a mock of it.
"""

import pytest
from mcp.client import Client
from oncall_contracts.config import Transport  # noqa: F401 -- asserts the vocabulary exists
from oncall_tools.registry import (
    SERVERS,
    TRANSPORT_ENV,
    ToolCallError,
    ToolRegistry,
    ToolSpec,
    UnknownServerError,
    _is_write,
    _transport_from_env,
    load_tools,
)
from oncall_tools.servers.runbooks_kb import build_server


@pytest.fixture
def registry():
    """The real server, in-process, on the lexical baseline."""
    with load_tools(connect=lambda _server: Client(build_server(semantic=False))) as loaded:
        yield loaded


# --- what the registry contains ------------------------------------------


def test_the_registry_holds_the_servers_tools(registry):
    assert sorted(registry.names) == ["get_runbook", "search_runbooks"]


def test_tools_carry_the_servers_own_schema(registry):
    """Passed through untouched. The server owns its contract; a schema rewritten
    here would be a second copy that drifts from what the server accepts."""
    schema = registry["search_runbooks"].schema

    assert sorted(schema["properties"]) == ["k", "query"]


def test_tools_carry_the_servers_description(registry):
    assert "score" in registry["search_runbooks"].description


def test_a_missing_tool_names_what_is_there(registry):
    with pytest.raises(KeyError, match="search_runbooks"):
        registry["promql"]


def test_the_registry_is_a_collection(registry):
    assert len(registry) == 2
    assert "get_runbook" in registry
    assert {tool.name for tool in registry} == set(registry.names)


# --- write marking: off the wire, fail-closed ----------------------------


def test_the_runbooks_tools_are_marked_read(registry):
    """Both are annotated read_only_hint=True by the server."""
    assert all(tool.read for tool in registry)
    assert registry.writes().names == []


def test_a_tool_with_no_annotations_is_treated_as_a_write():
    """Fail closed. A server author who forgets to annotate gets their tool kept
    OUT of diagnosis registries, rather than trusted into them."""
    assert _is_write(None) is True


def test_an_explicit_false_hint_is_a_write():
    assert _is_write(type("A", (), {"read_only_hint": False})()) is True


def test_only_an_explicit_true_hint_is_a_read():
    assert _is_write(type("A", (), {"read_only_hint": True})()) is False


def test_a_hint_that_is_merely_truthy_is_not_enough():
    """`is not True`, not `not ...`: a string, a 1 or a Mock attribute must not
    read as a declaration of read-only."""
    assert _is_write(type("A", (), {"read_only_hint": "yes"})()) is True


# --- invoking ------------------------------------------------------------


def test_invoke_takes_a_mapping(registry):
    """How the Tool stubs in oncall_graph are called."""
    hits = registry["search_runbooks"].invoke({"query": "emailMemoryLeak", "k": 2})

    assert hits
    assert hits[0]["document_id"] == "email-memory-leak"


def test_invoke_takes_keyword_arguments(registry):
    """How a LangChain StructuredTool calls it on the other side of the seam."""
    hits = registry["search_runbooks"].invoke(query="emailMemoryLeak", k=2)

    assert hits[0]["document_id"] == "email-memory-leak"


def test_invoke_returns_an_object_result_unwrapped(registry):
    runbook = registry["get_runbook"].invoke({"document_id": "payment-failure"})

    assert runbook["document_id"] == "payment-failure"
    assert runbook["fix"]["action"] == "set_flag"


def test_a_failing_tool_raises_rather_than_returning_nothing(registry):
    """A tool that errored must not read to the model as a tool that found
    nothing -- that distinction is what the whole corpus design rests on."""
    with pytest.raises(ToolCallError, match="get_runbook"):
        registry["get_runbook"].invoke({"document_id": "no-such-runbook"})


def test_the_same_session_serves_repeated_calls(registry):
    """If each call re-handshook, stdio would respawn a subprocess and reload an
    80 MB model every time. Two calls must not rebuild anything."""
    first = registry["search_runbooks"].invoke({"query": "payment", "k": 1})
    second = registry["search_runbooks"].invoke({"query": "payment", "k": 1})

    assert first == second


# --- narrowed views ------------------------------------------------------


def test_reads_and_writes_partition_the_registry(registry):
    assert len(registry.reads()) + len(registry.writes()) == len(registry)


def test_a_narrowed_view_shares_the_sessions_and_closes_nothing(registry):
    """A role view is a lens, not a second connection pool: closing it must not
    take the live sessions down under the registry that owns them."""
    view = registry.reads()
    view.close()

    assert registry["search_runbooks"].invoke({"query": "payment", "k": 1})


# --- servers -------------------------------------------------------------


def test_only_servers_that_exist_are_listed():
    """telemetry, tickets and ops_actions are unwritten. Listing them would make
    load_tools() fail for everyone until all four landed, or return fewer tools
    than it claimed."""
    assert sorted(SERVERS) == ["runbooks"]


def test_an_unknown_server_is_loud():
    """Not skipped: a registry that quietly omits a server the caller asked for
    produces an agent that cannot explain why it never searched runbooks."""
    with pytest.raises(UnknownServerError, match="telemetry"):
        load_tools(servers=["telemetry"])


def test_the_server_table_knows_the_port_and_module():
    server = SERVERS["runbooks"]

    assert server.port == 8102
    assert server.module == "oncall_tools.servers.runbooks_kb"
    assert server.url("mcp-runbooks") == "http://mcp-runbooks:8102/mcp"


# --- transport selection -------------------------------------------------


def test_the_transport_defaults_to_stdio(monkeypatch):
    """A developer who sets nothing gets subprocesses; Compose sets http."""
    monkeypatch.delenv(TRANSPORT_ENV, raising=False)

    assert _transport_from_env() == "stdio"


@pytest.mark.parametrize("value", ["http", "HTTP", " http "])
def test_the_transport_comes_from_the_environment(monkeypatch, value):
    monkeypatch.setenv(TRANSPORT_ENV, value)

    assert _transport_from_env() == "http"


def test_an_unrecognised_transport_raises_rather_than_falling_back(monkeypatch):
    """`MCP_TRANSPORT=htttp` silently meaning stdio is how a container ends up
    spawning subprocesses nobody can see."""
    monkeypatch.setenv(TRANSPORT_ENV, "htttp")

    with pytest.raises(ValueError, match="htttp"):
        _transport_from_env()


# --- lifecycle -----------------------------------------------------------


def test_close_is_idempotent():
    loaded = load_tools(connect=lambda _s: Client(build_server(semantic=False)))
    loaded.close()
    loaded.close()


def test_the_registry_can_be_built_without_the_context_manager():
    loaded = load_tools(connect=lambda _s: Client(build_server(semantic=False)))
    try:
        assert loaded.names
    finally:
        loaded.close()


def test_toolspec_read_is_the_inverse_of_write():
    spec = ToolSpec("t", "d", {}, write=True, server="s", invoke=lambda *_a, **_k: None)

    assert spec.write is True
    assert spec.read is False


def test_an_empty_registry_is_legal():
    """A role view can legitimately be empty -- a diagnosis view of a
    write-only server is nothing at all, and that is not an error."""
    assert len(ToolRegistry([])) == 0
