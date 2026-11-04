"""Live telemetry is opt-in. The default shop stays the scripted fixture."""

import json
from io import BytesIO
from urllib.request import Request

from oncall_graph.astronomy_shop import AstronomyShop, LiveShop
from oncall_graph.stubs import Shop
from oncall_graph.topologies.c1_single import Solo


class _Silent:
    model_id = "test"

    def invoke(self, messages):
        raise AssertionError("the stub shop test does not call a model")


def test_default_solo_shop_is_the_stub(capsys):
    solo = Solo(model=_Silent())
    assert type(solo.shop) is Shop
    assert solo.shop.promql("checkout_error_rate")["fault"] == "paymentFailure"
    logged = capsys.readouterr().out
    assert "scripted shop promql" in logged
    assert "astronomy shop" not in logged


def test_promql_sends_the_query_to_prometheus(monkeypatch, capsys):
    seen = {}

    def fake_urlopen(url, timeout=30):
        seen["url"] = url
        body = json.dumps(
            {
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [{"metric": {"service_name": "payment"}, "values": [[1, "0.2"]]}],
                },
            }
        ).encode()
        return BytesIO(body)

    monkeypatch.setattr("oncall_graph.astronomy_shop.urlopen", fake_urlopen)
    result = AstronomyShop(host="lab.example").promql("checkout_error_rate")
    assert "query=checkout_error_rate" in seen["url"]
    assert seen["url"].startswith("http://lab.example:9090/api/v1/query_range?")
    assert result["series"] == [{"metric": {"service_name": "payment"}, "value": [1, "0.2"]}]
    logged = capsys.readouterr().out
    assert "astronomy shop GET http://lab.example:9090/api/v1/query_range?" in logged
    assert "astronomy shop promql ok series=1" in logged


def test_independent_telemetry_urls_support_compose_service_names(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus:9090/")
    monkeypatch.setenv("JAEGER_URL", "http://jaeger:16686/jaeger/ui/")
    monkeypatch.setenv("OPENSEARCH_URL", "http://opensearch:9200/")

    shop = AstronomyShop(host="ignored.example")

    assert shop.prometheus == "http://prometheus:9090"
    assert shop.jaeger == "http://jaeger:16686/jaeger/ui"
    assert shop.opensearch == "http://opensearch:9200"


def test_log_search_posts_the_service(monkeypatch, capsys):
    seen = {}

    def fake_urlopen(request, timeout=30):
        assert isinstance(request, Request)
        seen["url"] = request.full_url
        seen["body"] = json.loads(request.data.decode())
        body = json.dumps(
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
        return BytesIO(body)

    monkeypatch.setattr("oncall_graph.astronomy_shop.urlopen", fake_urlopen)
    result = AstronomyShop(host="lab.example").log_search("payment", "timeout")
    assert seen["url"] == "http://lab.example:32794/otel-logs-*/_search"
    assert {"term": {"resource.service.name": "payment"}} in seen["body"]["query"]["bool"]["filter"]
    assert result["lines"] == ["2026-11-02T00:00:00Z ERROR payment timed out"]
    assert result["total"] == 1
    logged = capsys.readouterr().out
    assert "astronomy shop POST http://lab.example:32794/otel-logs-*/_search" in logged
    assert "astronomy shop log_search ok service='payment' total=1" in logged


def test_traces_asks_jaeger_for_the_service(monkeypatch, capsys):
    seen = {}

    def fake_urlopen(url, timeout=30):
        seen["url"] = url
        body = json.dumps({"data": [{"traceID": "abc"}, {"traceID": "def"}]}).encode()
        return BytesIO(body)

    monkeypatch.setattr("oncall_graph.astronomy_shop.urlopen", fake_urlopen)
    result = AstronomyShop(host="lab.example").traces("checkout")
    assert "query.serviceName=checkout" in seen["url"]
    assert seen["url"].startswith("http://lab.example:32797/jaeger/ui/api/v3/traces?")
    assert result["trace_ids"] == ["abc", "def"]
    logged = capsys.readouterr().out
    assert "astronomy shop GET http://lab.example:32797/jaeger/ui/api/v3/traces?" in logged
    assert "astronomy shop traces ok service='checkout' count=2" in logged


def test_live_shop_reads_telemetry_and_keeps_stub_writes(monkeypatch):
    monkeypatch.setattr(
        AstronomyShop,
        "promql",
        lambda self, query: {"query": query, "series": []},
    )
    shop = LiveShop(host="lab.example")
    assert shop.promql("up") == {"query": "up", "series": []}
    assert shop.reads[-1] == ("promql", {"query": "up"})
    assert shop.flags["paymentFailure"] is True
    shop.apply("set_flag", {"flag": "paymentFailure", "enabled": False})
    assert shop.flags["paymentFailure"] is False
