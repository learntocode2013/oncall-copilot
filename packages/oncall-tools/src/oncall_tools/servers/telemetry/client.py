"""HTTP clients for Prometheus, Jaeger and OpenSearch. Read-only.

MOVED HERE FROM `oncall_graph.astronomy_shop` (P2's package), unchanged in what
it queries and how it truncates. The move was forced and is the point: `make
guards` lets `oncall_tools` import `oncall_contracts` and `oncall_knowledge`
and NOTHING ELSE, so a telemetry server could not have wrapped P2's class. It
had to move, and `oncall_graph` may import `oncall_tools`, so `LiveShop` reads
it back from here. One definition, which is what #21 cost us to learn.

THREE THINGS CHANGED IN THE MOVE, each of them a bug this package cannot carry:

1. NO DEFAULT HOST. The old module defaulted to `10.241.2.69:9090`, Jaeger on
   `32797` and OpenSearch on `32794` -- NodePorts of a Kubernetes cluster that
   is not this stack. Compose overrides all three, so the wrong defaults were
   invisible in Docker and only bit a developer running from the host. The
   three URLs are now required: absent, this raises instead of pointing at a
   machine nobody owns. Same reason `runbooks_kb.configure` refuses to fall
   back to the lexical baseline -- a server that quietly serves the wrong thing
   is worse than one that will not start.

2. AN UNREACHABLE BACKEND RAISES. The old methods caught `URLError` and
   returned `{"error": ...}`, so a dead Prometheus and a quiet one were the
   same evidence. `registry.ToolCallError` exists for exactly this: "a failed
   tool cannot read to the model as a tool that found nothing". The tools raise
   `TelemetryUnavailable`, which the server turns into a `ToolError` so the
   reason reaches the caller (PR #20).

3. IT LOGS TO A LOGGER, NOT TO STDOUT. The old code traced through
   `oncall_graph.meter.say`, which is a `print`. In a stdio server stdout IS
   the JSON-RPC channel, so those lines would corrupt the framing of every
   response. The trace lines `solo_demo` prints now come from `LiveShop`.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

#: Ports the Compose stack publishes, used only to expand the `host=`
#: shorthand. NOT a fallback for a missing URL -- see `_endpoints`.
PROMETHEUS_PORT = 9090
JAEGER_PORT = 16686
OPENSEARCH_PORT = 9200

PROMETHEUS_ENV = "PROMETHEUS_URL"
JAEGER_ENV = "JAEGER_URL"
OPENSEARCH_ENV = "OPENSEARCH_URL"

#: One host serving all three on the published ports, as an environment
#: variable. Kept from the module this moved from, because `solo_demo` calls
#: `LiveShop()` with no arguments and asking a developer for three variables
#: where one will do buys no safety. It is a SHORTHAND, not a default: unset,
#: nothing is assumed and construction fails.
HOST_ENV = "ASTRONOMY_SHOP_HOST"

#: Every variable this server is configured by. `registry.ServerSpec.env` names
#: the same three, and `test_telemetry_env_matches_the_registry` pins them to
#: each other -- a spawned server is handed only what its spec lists, and the
#: one that was missing from `ops_actions` cost us PR #20.
CONFIG_ENV = (PROMETHEUS_ENV, JAEGER_ENV, OPENSEARCH_ENV, HOST_ENV)

#: A shape check on a service name, not an allowlist. The old code's error said
#: "not allow-listed", which claimed a control that does not exist: every real
#: service name matches this, and so does `not-a-service`. What it actually
#: prevents is a name carrying a path, a query string or whitespace into a URL.
_SERVICE = re.compile(r"^[A-Za-z0-9_.-]+$")

_MAX_SERIES = 15
_MAX_LINES = 20
_MAX_LINE = 240
_MAX_TRACES = 20
_MAX_USERS = 20
_USER_KEYS = ("user.id", "enduser.id", "app.user.id")

_TIMEOUT = 30


class TelemetryUnavailable(RuntimeError):
    """A telemetry backend could not be reached, or did not answer with JSON.

    Raised rather than returned. An agent that receives `{"error": "connection
    refused"}` as a tool result reads it as telemetry, and "no anomaly found"
    is the conclusion it draws from a backend that was never asked. The server
    re-raises this as `ToolError` so the text survives the wire.
    """


class TelemetryNotConfigured(RuntimeError):
    """One of the three URLs is missing and no `host` was given.

    Raised while the server is being built, before the transport binds a port,
    so the container fails its health check instead of answering every query
    with an unreachable endpoint.
    """


def _endpoints(
    host: str | None,
    prometheus_url: str | None,
    jaeger_url: str | None,
    opensearch_url: str | None,
) -> tuple[str, str, str]:
    """Resolve the three endpoints: explicit argument, then env, then `host`.

    `host` expands to the three published ports, which is the shorthand the
    tests and a developer on `make -C deploy up` want. With neither a URL nor a
    host for any one of them, this raises -- naming all the missing ones at
    once, because a developer who set none should not fix them one error at a
    time.
    """
    one_host = host or os.environ.get(HOST_ENV) or None
    resolved = {}
    missing = []
    for name, explicit, variable, port in (
        ("prometheus", prometheus_url, PROMETHEUS_ENV, PROMETHEUS_PORT),
        ("jaeger", jaeger_url, JAEGER_ENV, JAEGER_PORT),
        ("opensearch", opensearch_url, OPENSEARCH_ENV, OPENSEARCH_PORT),
    ):
        chosen = (
            explicit
            or os.environ.get(variable)
            or (f"http://{one_host}:{port}" if one_host else None)
        )
        if not chosen:
            missing.append(variable)
        else:
            resolved[name] = chosen.rstrip("/")
    if missing:
        raise TelemetryNotConfigured(
            f"telemetry is not configured: set {', '.join(missing)}, or set {HOST_ENV} "
            f"to one host serving all three on ports {PROMETHEUS_PORT}/{JAEGER_PORT}/"
            f"{OPENSEARCH_PORT}. There is no default host: the previous one was a "
            "Kubernetes NodePort address that is not this stack."
        )
    return resolved["prometheus"], resolved["jaeger"], resolved["opensearch"]


class AstronomyShop:
    """Prometheus, Jaeger and OpenSearch, over the last `minutes` minutes.

    Constructing this opens no connection. It does resolve the three endpoints,
    so a missing one is a construction error rather than a per-query one.
    """

    def __init__(
        self,
        host: str | None = None,
        minutes: int = 5,
        *,
        prometheus_url: str | None = None,
        jaeger_url: str | None = None,
        opensearch_url: str | None = None,
    ):
        self.minutes = minutes
        self.prometheus, self.jaeger, self.opensearch = _endpoints(
            host, prometheus_url, jaeger_url, opensearch_url
        )

    def _window(self):
        end = datetime.now(UTC).replace(microsecond=0)
        start = end - timedelta(minutes=self.minutes)
        return start, end

    def _get(self, url):
        log.info("telemetry GET %s", url)
        try:
            with urlopen(url, timeout=_TIMEOUT) as response:
                return json.load(response)
        except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise TelemetryUnavailable(f"GET {url} failed: {exc}") from exc

    def _post(self, url, body):
        log.info("telemetry POST %s", url)
        request = Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=_TIMEOUT) as response:
                return json.load(response)
        except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise TelemetryUnavailable(f"POST {url} failed: {exc}") from exc

    def promql(self, query: str) -> dict:
        """Run one PromQL range query over the window. Last value per series."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("promql query must be a non-empty string")
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
        # Prometheus answers a bad query with HTTP 400 and {"status": "error",
        # "error": "..."}. urlopen raises on 400, so this branch catches the
        # case where it answered 200 and still reported an error.
        if payload.get("error") and payload.get("status") != "success":
            raise TelemetryUnavailable(f"prometheus rejected {query!r}: {payload.get('error')}")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        series = []
        for item in (data.get("result") or [])[:_MAX_SERIES]:
            if not isinstance(item, dict):
                continue
            values = item.get("values") or []
            last = values[-1] if values else None
            series.append({"metric": item.get("metric") or {}, "value": last})
        log.info("telemetry promql ok series=%d query=%r", len(series), query)
        return {
            "query": query,
            "result_type": data.get("resultType"),
            "series": series,
            "window_minutes": self.minutes,
        }

    def log_search(self, service: str, query: str = "") -> dict:
        """Recent log lines for one service, newest first."""
        if not isinstance(service, str) or not _SERVICE.match(service):
            raise ValueError(
                f"service {service!r} is not a service name: "
                "letters, digits, dot, dash and underscore only"
            )
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
            raise TelemetryUnavailable(f"opensearch rejected the search: {payload['error']}")
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
        log.info("telemetry log_search ok service=%r total=%s", service, total or 0)
        return {
            "service": service,
            "query": query or "",
            "total": total or 0,
            "lines": lines,
            "window_minutes": self.minutes,
        }

    def traces(self, service: str, search_depth: int = _MAX_TRACES) -> dict:
        """Recent trace ids for one service. Span bodies stay on Jaeger."""
        if not isinstance(service, str) or not _SERVICE.match(service):
            raise ValueError(
                f"service {service!r} is not a service name: "
                "letters, digits, dot, dash and underscore only"
            )
        start, end = self._window()
        encoded = urlencode(
            {
                "query.serviceName": service,
                "query.startTimeMin": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "query.startTimeMax": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "query.searchDepth": search_depth,
            }
        )
        payload = self._get(f"{self.jaeger}/api/v3/traces?{encoded}")
        if payload.get("error") and not isinstance(payload.get("data"), (dict, list)):
            raise TelemetryUnavailable(f"jaeger rejected the search: {payload['error']}")
        found: list[str] = []
        _collect_trace_ids(payload, found)
        users: set[str] = set()
        errors: list[str] = []
        _collect_span_facts(payload, users, errors)
        log.info(
            "telemetry traces ok service=%r count=%d errors=%d", service, len(found), len(errors)
        )
        return {
            "service": service,
            "count": len(found),
            "trace_ids": found,
            "users": sorted(users)[:_MAX_USERS],
            "error_trace_ids": errors[:_MAX_TRACES],
            "window_minutes": self.minutes,
        }


def _collect_trace_ids(payload, found: list[str]) -> None:
    """Walk the payload for trace ids. Jaeger's v3 shape varies by version."""
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


def _is_error_span(node: dict, attributes: dict) -> bool:
    status = node.get("status")
    if isinstance(status, dict) and str(status.get("code") or "") in ("STATUS_CODE_ERROR", "2"):
        return True
    if "ERROR" in str((attributes.get("otel.status_code") or {}).get("stringValue") or ""):
        return True
    return (attributes.get("error") or {}).get("boolValue") is True


def _collect_span_facts(payload, users: set[str], errors: list[str]) -> None:
    """User ids from span attributes, and the trace ids of spans that errored.

    OTLP JSON spans carry ``attributes`` as a list of ``{key, value}`` objects.
    """
    if isinstance(payload, dict):
        raw = payload.get("attributes")
        if isinstance(raw, list):
            attributes = {
                item.get("key"): item.get("value") or {}
                for item in raw
                if isinstance(item, dict) and isinstance(item.get("value") or {}, dict)
            }
            for key in _USER_KEYS:
                value = (attributes.get(key) or {}).get("stringValue")
                if isinstance(value, str) and value:
                    users.add(value)
            trace_id = payload.get("traceId") or payload.get("traceID")
            if (
                isinstance(trace_id, str)
                and trace_id not in errors
                and _is_error_span(payload, attributes)
            ):
                errors.append(trace_id)
        for value in payload.values():
            _collect_span_facts(value, users, errors)
    elif isinstance(payload, list):
        for item in payload:
            _collect_span_facts(item, users, errors)
