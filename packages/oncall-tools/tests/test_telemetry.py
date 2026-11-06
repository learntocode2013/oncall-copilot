"""The telemetry client and the server over it.

Moved here with the client from `oncall_graph/tests/test_astronomy_shop.py`.
The query-shape tests are that file's, unchanged in what they assert about the
URLs and the payloads. What is new is the three behaviours the move changed:
construction refuses a missing endpoint, an unreachable backend raises, and the
reason survives the MCP wire as a `ToolError`.
"""

import json
import logging
from io import BytesIO
from urllib.error import URLError
from urllib.request import Request

import pytest
from mcp.client import Client
from oncall_tools.registry import SERVERS, ToolCallError, load_tools
from oncall_tools.servers.telemetry import build_server
from oncall_tools.servers.telemetry.client import (
    CONFIG_ENV,
    AstronomyShop,
    TelemetryNotConfigured,
    TelemetryUnavailable,
)

LAB = {
    "prometheus_url": "http://lab.example:9090",
    "jaeger_url": "http://lab.example:16686/jaeger/ui",
    "opensearch_url": "http://lab.example:9200",
}


@pytest.fixture(autouse=True)
def _no_ambient_urls(monkeypatch):
    """A developer's own PROMETHEUS_URL must not reach into these assertions."""
    for name in CONFIG_ENV:
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------


def test_a_missing_endpoint_refuses_to_construct():
    """There is no default host. The old one was 10.241.2.69, a NodePort
    address of a cluster that is not this stack, and because Compose overrode
    all three URLs it was invisible in Docker and wrong everywhere else."""
    with pytest.raises(TelemetryNotConfigured) as caught:
        AstronomyShop()
    message = str(caught.value)
    assert "PROMETHEUS_URL" in message
    assert "ASTRONOMY_SHOP_HOST" in message, "the refusal must name the one-variable way out"


def test_every_missing_endpoint_is_named_at_once(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus:9090")
    with pytest.raises(TelemetryNotConfigured) as caught:
        AstronomyShop()
    message = str(caught.value)
    assert "JAEGER_URL" in message and "OPENSEARCH_URL" in message
    assert "PROMETHEUS_URL" not in message, "the one that IS set must not be reported missing"


def test_the_host_shorthand_also_comes_from_the_environment(monkeypatch):
    """`solo_demo` calls `LiveShop()` with no arguments. One variable where
    three would do is a convenience, not a default: unset, nothing is assumed."""
    monkeypatch.setenv("ASTRONOMY_SHOP_HOST", "lab.example")

    shop = AstronomyShop()

    assert shop.prometheus == "http://lab.example:9090"
    assert shop.jaeger == "http://lab.example:16686"
    assert shop.opensearch == "http://lab.example:9200"


def test_an_explicit_url_still_beats_the_host_shorthand(monkeypatch):
    monkeypatch.setenv("ASTRONOMY_SHOP_HOST", "lab.example")
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus:9090")

    shop = AstronomyShop()

    assert shop.prometheus == "http://prometheus:9090"
    assert shop.jaeger == "http://lab.example:16686"


def test_host_expands_to_the_published_ports():
    """`host=` is the shorthand for one machine running all three.

    9090/16686/9200 are what `make -C deploy up` publishes. The old shorthand
    used 32797 and 32794 -- Kubernetes NodePorts, so the shorthand was wrong on
    every machine this project actually runs on.
    """
    shop = AstronomyShop(host="localhost")
    assert shop.prometheus == "http://localhost:9090"
    assert shop.jaeger == "http://localhost:16686"
    assert shop.opensearch == "http://localhost:9200"


def test_independent_telemetry_urls_support_compose_service_names(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus:9090/")
    monkeypatch.setenv("JAEGER_URL", "http://jaeger:16686/jaeger/ui/")
    monkeypatch.setenv("OPENSEARCH_URL", "http://opensearch:9200/")

    shop = AstronomyShop(host="ignored.example")

    assert shop.prometheus == "http://prometheus:9090"
    assert shop.jaeger == "http://jaeger:16686/jaeger/ui"
    assert shop.opensearch == "http://opensearch:9200"


def test_telemetry_env_matches_the_registry():
    """The spec's `env` and the client's `CONFIG_ENV` are one list in two files.

    A spawned server is handed ONLY what its spec names: leave a variable out
    and the SDK's `get_default_environment()` allowlist silently drops it. That
    is PR #20 exactly -- `ops_actions` lost FLAGD_CONFIG, wrote the committed
    baseline and reported success. Three variables is three chances to repeat it.
    """
    assert set(SERVERS["telemetry"].env) == set(CONFIG_ENV)


# --------------------------------------------------------------------------
# what each tool asks for
# --------------------------------------------------------------------------


def test_promql_sends_the_query_to_prometheus(monkeypatch, caplog):
    seen = {}

    def fake_urlopen(url, timeout=30):
        seen["url"] = url
        return BytesIO(
            json.dumps(
                {
                    "status": "success",
                    "data": {
                        "resultType": "matrix",
                        "result": [{"metric": {"service_name": "payment"}, "values": [[1, "0.2"]]}],
                    },
                }
            ).encode()
        )

    monkeypatch.setattr("oncall_tools.servers.telemetry.client.urlopen", fake_urlopen)
    with caplog.at_level(logging.INFO, logger="oncall_tools.servers.telemetry.client"):
        result = AstronomyShop(**LAB).promql("checkout_error_rate")

    assert "query=checkout_error_rate" in seen["url"]
    assert seen["url"].startswith("http://lab.example:9090/api/v1/query_range?")
    assert result["series"] == [{"metric": {"service_name": "payment"}, "value": [1, "0.2"]}]
    assert "telemetry promql ok series=1" in caplog.text


def test_the_client_never_writes_to_stdout(monkeypatch, capsys):
    """stdout is the JSON-RPC channel of a stdio server.

    The client this moved from traced through `oncall_graph.meter.say`, which is
    a `print`. One of those lines interleaved with a response would corrupt the
    framing of the session, and the symptom would be a parse error nowhere near
    the cause.
    """
    monkeypatch.setattr(
        "oncall_tools.servers.telemetry.client.urlopen",
        lambda url, timeout=30: BytesIO(b'{"status":"success","data":{"result":[]}}'),
    )
    AstronomyShop(**LAB).promql("up")
    assert capsys.readouterr().out == ""


def test_log_search_posts_the_service(monkeypatch, caplog):
    seen = {}

    def fake_urlopen(request, timeout=30):
        assert isinstance(request, Request)
        seen["url"] = request.full_url
        seen["body"] = json.loads(request.data.decode())
        return BytesIO(
            json.dumps(
                {
                    "hits": {
                        "total": {"value": 1},
                        "hits": [
                            {
                                "_source": {
                                    "@timestamp": "2026-11-02T00:00:00Z",
                                    "severity.text": "ERROR",
                                    "body": "payment timed out",
                                }
                            }
                        ],
                    }
                }
            ).encode()
        )

    monkeypatch.setattr("oncall_tools.servers.telemetry.client.urlopen", fake_urlopen)
    with caplog.at_level(logging.INFO, logger="oncall_tools.servers.telemetry.client"):
        result = AstronomyShop(**LAB).log_search("payment", "timeout")

    assert seen["url"] == "http://lab.example:9200/otel-logs-*/_search"
    assert {"term": {"resource.service.name": "payment"}} in seen["body"]["query"]["bool"]["filter"]
    assert result["lines"] == ["2026-11-02T00:00:00Z ERROR payment timed out"]
    assert result["total"] == 1
    assert "telemetry log_search ok service='payment' total=1" in caplog.text


def test_traces_asks_jaeger_for_the_service(monkeypatch):
    seen = {}

    def fake_urlopen(url, timeout=30):
        seen["url"] = url
        return BytesIO(json.dumps({"data": [{"traceID": "abc"}, {"traceID": "def"}]}).encode())

    monkeypatch.setattr("oncall_tools.servers.telemetry.client.urlopen", fake_urlopen)
    result = AstronomyShop(**LAB).traces("checkout")

    assert "query.serviceName=checkout" in seen["url"]
    assert seen["url"].startswith("http://lab.example:16686/jaeger/ui/api/v3/traces?")
    assert result["trace_ids"] == ["abc", "def"]


def test_a_log_line_is_truncated_rather_than_returned_whole(monkeypatch):
    """240 characters per line, so one verbose service cannot fill the window."""

    def fake_urlopen(request, timeout=30):
        return BytesIO(
            json.dumps({"hits": {"total": 1, "hits": [{"_source": {"body": "x" * 5000}}]}}).encode()
        )

    monkeypatch.setattr("oncall_tools.servers.telemetry.client.urlopen", fake_urlopen)
    (line,) = AstronomyShop(**LAB).log_search("payment")["lines"]
    assert len(line) == 240


# --------------------------------------------------------------------------
# an unreachable backend is not an observation
# --------------------------------------------------------------------------


def test_an_unreachable_prometheus_raises_rather_than_returning_an_error_dict(monkeypatch):
    """The behaviour this move changed, and the reason it had to.

    The old client returned `{"query": ..., "error": "connection refused"}`. An
    agent handed that reads it as telemetry and concludes the metric is quiet.
    `registry.ToolCallError` says it outright: a failed tool must not read to
    the model as a tool that found nothing.
    """

    def refuse(url, timeout=30):
        raise URLError("Connection refused")

    monkeypatch.setattr("oncall_tools.servers.telemetry.client.urlopen", refuse)
    with pytest.raises(TelemetryUnavailable) as caught:
        AstronomyShop(**LAB).promql("up")
    assert "Connection refused" in str(caught.value)


def test_a_prometheus_error_payload_raises(monkeypatch):
    monkeypatch.setattr(
        "oncall_tools.servers.telemetry.client.urlopen",
        lambda url, timeout=30: BytesIO(b'{"status":"error","error":"parse error"}'),
    )
    with pytest.raises(TelemetryUnavailable, match="parse error"):
        AstronomyShop(**LAB).promql("not a query {{{")


def test_an_empty_result_is_an_answer_not_a_failure(monkeypatch):
    """`series: []` must NOT raise. Prometheus answering "nothing matched" is a
    real read, and the tool description says so; conflating it with an
    unreachable backend would hide the distinction this file exists to keep."""
    monkeypatch.setattr(
        "oncall_tools.servers.telemetry.client.urlopen",
        lambda url, timeout=30: BytesIO(
            b'{"status":"success","data":{"resultType":"matrix","result":[]}}'
        ),
    )
    assert AstronomyShop(**LAB).promql("up")["series"] == []


@pytest.mark.parametrize("service", ["", "has space", "payment/../etc", "a?b=c"])
def test_a_service_name_that_could_carry_a_path_is_refused(service):
    with pytest.raises(ValueError, match="service name"):
        AstronomyShop(**LAB).traces(service)


def test_an_empty_promql_query_is_refused():
    with pytest.raises(ValueError, match="non-empty"):
        AstronomyShop(**LAB).promql("   ")


# --------------------------------------------------------------------------
# over MCP
# --------------------------------------------------------------------------


class _Fake:
    """Stands in for the client, so the server tests exercise the server."""

    minutes = 5

    def __init__(self, raises=None):
        self.raises = raises
        self.calls = []

    def _answer(self, name, payload):
        self.calls.append((name, payload))
        if self.raises is not None:
            raise self.raises
        return payload

    def promql(self, query):
        return self._answer("promql", {"query": query, "series": [], "window_minutes": 5})

    def log_search(self, service, query=""):
        return self._answer("log_search", {"service": service, "lines": [], "total": 0})

    def traces(self, service):
        return self._answer("traces", {"service": service, "count": 0, "trace_ids": []})


def _registry(shop):
    return load_tools(servers=["telemetry"], connect=lambda _spec: Client(build_server(shop=shop)))


def test_the_server_serves_three_tools_and_all_of_them_are_reads():
    with _registry(_Fake()) as registry:
        assert sorted(registry.names) == ["log_search", "promql", "traces"]
        assert sorted(registry.reads().names) == ["log_search", "promql", "traces"]
        assert registry.writes().names == []


def test_a_telemetry_tool_carries_its_arguments_over_the_wire():
    shop = _Fake()
    with _registry(shop) as registry:
        result = registry["promql"].invoke({"query": "checkout_error_rate"})
    assert result["query"] == "checkout_error_rate"
    assert shop.calls == [("promql", result)]


def test_an_unreachable_backend_reaches_the_caller_with_its_reason():
    """PR #20's lesson, applied before it could be repeated.

    `ToolError` text crosses the wire; any other exception becomes
    `UnexpectedToolError` and the agent gets a bare "Error executing tool
    promql" while the reason stays in the server's log.
    """
    unreachable = _Fake(raises=TelemetryUnavailable("GET http://dead:9090 failed"))
    with _registry(unreachable) as registry, pytest.raises(ToolCallError) as caught:
        registry["promql"].invoke({"query": "up"})
    assert "http://dead:9090" in str(caught.value)


def test_a_bad_service_name_reaches_the_caller_with_its_reason():
    bad_name = _Fake(raises=ValueError("service 'a b' is not a service name"))
    with _registry(bad_name) as registry, pytest.raises(ToolCallError, match="not a service name"):
        registry["traces"].invoke({"service": "a b"})


def test_building_the_server_fails_before_a_port_is_bound(monkeypatch):
    """An unconfigured server must not start and report healthy.

    `mcp-runbooks` warms its retriever in `build_server` for this reason:
    measured there, the port answered in 2 seconds while retrieval was still
    broken. Resolving the endpoints in `build_server` puts the same failure
    before `_serve.run`.
    """
    for name in CONFIG_ENV:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(TelemetryNotConfigured):
        build_server()
