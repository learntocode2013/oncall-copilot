"""Read-only telemetry from the Astronomy Shop lab.

``promql``, ``log_search``, and ``traces`` are the three tools the solo demo
calls. Writes, verification, and the other reads stay on the scripted shop.
Importing this module does not open a connection.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime, timedelta
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .meter import say
from .stubs import Shop

DEFAULT_HOST = "10.241.2.69"
_SERVICE = re.compile(r"^[A-Za-z0-9_.-]+$")
_MAX_SERIES = 15
_MAX_LINES = 20
_MAX_LINE = 240
_MAX_TRACES = 20


class AstronomyShop:
    """Prometheus, Jaeger, and OpenSearch on one lab host."""

    def __init__(self, host: str | None = None, minutes: int = 5):
        self.minutes = minutes
        chosen = host or os.environ.get("ASTRONOMY_SHOP_HOST") or DEFAULT_HOST
        self.prometheus = f"http://{chosen}:9090"
        self.jaeger = f"http://{chosen}:32797"
        self.opensearch = f"http://{chosen}:32794"

    def _window(self):
        end = datetime.now(UTC).replace(microsecond=0)
        start = end - timedelta(minutes=self.minutes)
        return start, end

    def _get(self, url):
        say(f"      astronomy shop GET {url}")
        try:
            with urlopen(url, timeout=30) as response:
                return json.load(response)
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            say(f"      astronomy shop GET failed: {exc}")
            return {"error": str(exc)}

    def _post(self, url, body):
        say(f"      astronomy shop POST {url}")
        request = Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            say(f"      astronomy shop POST failed: {exc}")
            return {"error": str(exc)}

    def promql(self, query: str) -> dict:
        """Run one PromQL range query. The series payload stays in this result."""
        if not isinstance(query, str) or not query.strip():
            say("      astronomy shop promql skipped: query must be a non-empty string")
            return {"query": query, "error": "promql query must be a non-empty string"}
        start, end = self._window()
        encoded = urlencode(
            {
                "query": query,
                "start": int(start.timestamp()),
                "end": int(end.timestamp()),
                "step": 60,
            }
        )
        payload = self._get(f"{self.prometheus}/api/v1/query_range?{encoded}")
        if payload.get("error") and payload.get("status") != "success":
            say(f"      astronomy shop promql error: {payload.get('error')}")
            return {"query": query, "error": str(payload.get("error"))}
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        series = []
        for item in (data.get("result") or [])[:_MAX_SERIES]:
            if not isinstance(item, dict):
                continue
            values = item.get("values") or []
            last = values[-1] if values else None
            series.append({"metric": item.get("metric") or {}, "value": last})
        say(f"      astronomy shop promql ok series={len(series)} query={query!r}")
        return {
            "query": query,
            "result_type": data.get("resultType"),
            "series": series,
        }

    def log_search(self, service: str, query: str = "") -> dict:
        """Recent log lines for one service. The lines are data for this turn."""
        if not isinstance(service, str) or not _SERVICE.match(service):
            say(f"      astronomy shop log_search skipped: service {service!r} is not allow-listed")
            return {"service": service, "query": query, "error": "service name is not allow-listed"}
        filters = [
            {"term": {"resource.service.name": service}},
            {"range": {"@timestamp": {"gte": f"now-{self.minutes}m"}}},
        ]
        if isinstance(query, str) and query.strip():
            filters.append({"match_phrase": {"body": query}})
        payload = self._post(
            f"{self.opensearch}/otel-logs-*/_search",
            {
                "size": _MAX_LINES,
                "sort": [{"@timestamp": "desc"}],
                "track_total_hits": True,
                "_source": ["@timestamp", "severity.text", "body", "traceId"],
                "query": {"bool": {"filter": filters}},
            },
        )
        if payload.get("error") and "hits" not in payload:
            say(f"      astronomy shop log_search error: {payload['error']}")
            return {"service": service, "query": query, "error": str(payload["error"])}
        hits = (payload.get("hits") or {}) if isinstance(payload.get("hits"), dict) else {}
        total = hits.get("total")
        if isinstance(total, dict):
            total = total.get("value")
        lines = []
        for hit in (hits.get("hits") or [])[:_MAX_LINES]:
            source = hit.get("_source") if isinstance(hit, dict) else None
            if not isinstance(source, dict):
                continue
            body = str(source.get("body") or "")[:_MAX_LINE]
            lines.append(
                f"{source.get('@timestamp') or ''} {source.get('severity.text') or ''} {body}".strip()
            )
        say(f"      astronomy shop log_search ok service={service!r} total={total or 0}")
        return {"service": service, "query": query or "", "total": total or 0, "lines": lines}

    def traces(self, service: str, search_depth: int = _MAX_TRACES) -> dict:
        """Recent trace ids for one service. Span bodies stay on Jaeger."""
        if not isinstance(service, str) or not _SERVICE.match(service):
            say(f"      astronomy shop traces skipped: service {service!r} is not allow-listed")
            return {"service": service, "error": "service name is not allow-listed"}
        start, end = self._window()
        encoded = urlencode(
            {
                "query.serviceName": service,
                "query.startTimeMin": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "query.startTimeMax": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "query.searchDepth": search_depth,
            }
        )
        payload = self._get(f"{self.jaeger}/jaeger/ui/api/v3/traces?{encoded}")
        if payload.get("error") and not isinstance(payload.get("data"), (dict, list)):
            say(f"      astronomy shop traces error: {payload['error']}")
            return {"service": service, "error": str(payload["error"])}
        found: list[str] = []
        _collect_trace_ids(payload, found)
        say(f"      astronomy shop traces ok service={service!r} count={len(found)}")
        return {"service": service, "count": len(found), "trace_ids": found}


class LiveShop(Shop):
    """Scripted writes and reads, with the three telemetry tools pointed at the lab."""

    def __init__(self, host: str | None = None, minutes: int = 5):
        super().__init__()
        self.telemetry = AstronomyShop(host=host, minutes=minutes)

    def promql(self, query: str) -> dict:
        self.reads.append(("promql", {"query": query}))
        return self.telemetry.promql(query)

    def log_search(self, service: str, query: str = "") -> dict:
        self.reads.append(("log_search", {"service": service, "query": query}))
        return self.telemetry.log_search(service, query)

    def traces(self, service: str) -> dict:
        self.reads.append(("traces", {"service": service}))
        return self.telemetry.traces(service)


def _collect_trace_ids(payload, found: list[str]) -> None:
    if len(found) >= _MAX_TRACES:
        return
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() in {"traceid", "trace_id"} and isinstance(value, str):
                if value not in found:
                    found.append(value)
            else:
                _collect_trace_ids(value, found)
    elif isinstance(payload, list):
        for item in payload:
            _collect_trace_ids(item, found)
            if len(found) >= _MAX_TRACES:
                return
