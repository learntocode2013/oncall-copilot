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

#: Bounds on what `traces` returns about errored spans.
#:
#: 24 GROUPS, AND THE FIRST NUMBER HERE WAS 8. Measured against the live stack
#: with `paymentFailure` armed: Jaeger returned 28 errored spans in 10 groups,
#: and the two that mattered -- `payment | charge` and `payment |
#: oteldemo.PaymentService/Charge`, one span each -- were ranked LAST, because
#: the window also held 20 spans of routine `flagd`/`cart` flag-evaluation
#: noise. A bound of 8 cut exactly the two rows the whole investigation is
#: about, and the result still read as a complete list of errors. Hence 24, and
#: hence `error_groups` and `error_groups_omitted` in the result: the totals are
#: counted over every group, so a truncated list says that it is one.
_MAX_ERROR_GROUPS = 24
_MAX_TRACE_CITATIONS = 3
_MAX_NAME = 120

#: Control characters are stripped from a span or service name so one field
#: cannot impersonate the end of a row. See `_name`.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

#: OTLP `Status.code` 2 is ERROR (0 UNSET, 1 OK). A JSON encoder may emit the
#: enum as a number, a numeric string or its name, so all four are accepted --
#: and anything else is NOT an error, which keeps an unrecognised encoding from
#: reading as a healthy span.
_STATUS_ERROR = frozenset({2, "2", "ERROR", "STATUS_CODE_ERROR"})

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
        """Recent traces for one service, with the spans that carried an error.

        WHAT THIS RETURNS AND WHAT IT DELIBERATELY DOES NOT.

        Four facts per failing span group: the service the span belongs to, the
        span's operation name, how many error spans matched, and up to three
        trace ids to cite. Nothing else crosses from a span into this result --
        no attributes, no `status.message`, no events, no log bodies. A trace
        from a service under an injected fault is a long stretch of text written
        by the thing being diagnosed, and `log_search`'s description already
        says why that text is data and not instructions. Here it simply does not
        travel: a name and a count cannot carry a sentence.

        `errors` reports the span's OWN service, which is usually not the
        `service` asked for. Jaeger returns traces that the queried service took
        part in, so asking about `checkout` is how you find out that `payment`
        errored inside its traces. That is a read, not a verdict: this server
        does not decide which of them is the cause.

        `spans_read` exists so an empty `errors` can be told apart from a
        payload this client could not walk. Jaeger returning traces whose spans
        cannot be read is a FAILED read, not a quiet system, so it raises --
        the same rule as an unreachable backend.
        """
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
        read = _read_error_spans(payload)
        if found and not read["spans_read"]:
            raise TelemetryUnavailable(
                f"jaeger returned {len(found)} trace(s) for {service!r} and no span this "
                "client could read: no `resourceSpans` block in the payload. Reporting "
                "zero errors here would mean 'I did not look', and that reads to an agent "
                "as 'nothing is wrong'. Check the Jaeger version's /api/v3/traces shape."
            )
        log.info(
            "telemetry traces ok service=%r count=%d spans=%d error_spans=%d groups=%d",
            service,
            len(found),
            read["spans_read"],
            read["error_spans"],
            read["error_groups"],
        )
        return {
            "service": service,
            "count": len(found),
            "trace_ids": found,
            "spans_read": read["spans_read"],
            "error_spans": read["error_spans"],
            "error_traces": read["error_traces"],
            "error_groups": read["error_groups"],
            "error_groups_omitted": read["error_groups"] - len(read["errors"]),
            "errors": read["errors"],
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


def _resource_spans(payload, blocks: list[dict]) -> None:
    """Collect every `resourceSpans` entry, wherever the payload nests it.

    Jaeger 2's `/api/v3/traces` answers with OTLP JSON under a `result` key, but
    the nesting has moved between versions and a grpc-gateway stream can wrap
    it again. Searching for the block rather than indexing a fixed path means a
    moved wrapper costs nothing; a RENAMED block is caught by `spans_read == 0`.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in {"resourceSpans", "resource_spans"} and isinstance(value, list):
                blocks.extend(item for item in value if isinstance(item, dict))
            else:
                _resource_spans(value, blocks)
    elif isinstance(payload, list):
        for item in payload:
            _resource_spans(item, blocks)


def _attribute(attributes, key: str) -> str:
    """One string-valued OTLP attribute out of an attribute list."""
    for item in attributes or []:
        if not isinstance(item, dict) or item.get("key") != key:
            continue
        value = item.get("value")
        if isinstance(value, dict):
            for form in ("stringValue", "string_value"):
                if isinstance(value.get(form), str):
                    return value[form]
        return ""
    return ""


def _name(value) -> str:
    """A service or operation name, bounded and on one line.

    Span names come from instrumentation rather than from a user, so this is not
    sanitising untrusted prose -- the untrusted prose is the part this tool does
    not return at all. It is making sure one field cannot pretend to be two: a
    newline or a control character inside a name would let it impersonate the
    end of the row in anything that renders this as text.
    """
    return _CONTROL.sub(" ", str(value or "")).strip()[:_MAX_NAME]


def _read_error_spans(payload) -> dict:
    """Count the spans in an OTLP payload and group the errored ones.

    Returns `spans_read`, `error_spans`, `error_traces`, `error_groups` and
    `errors`. THE FOUR COUNTS ARE OVER EVERYTHING FOUND; only `errors` is
    truncated. Summing the rows that survived truncation would under-report the
    errors in the window, and a tool that under-reports errors is the failure
    this project keeps finding: it answers, it looks complete, and it is wrong.

    `errors` is grouped by (service, operation) rather than listed per span,
    because a load generator under an injected fault produces the same failing
    span hundreds of times a minute and twenty copies of one row is not twenty
    pieces of evidence. The group is a count, not a ranking of blame: the
    loudest group in a measured run was routine flag-evaluation noise and the
    quietest was the injected fault.
    """
    blocks: list[dict] = []
    _resource_spans(payload, blocks)
    spans_read = 0
    error_spans = 0
    error_traces: set[str] = set()
    groups: dict[tuple[str, str], dict] = {}
    for block in blocks:
        resource = block.get("resource") if isinstance(block.get("resource"), dict) else {}
        service = _name(_attribute(resource.get("attributes"), "service.name")) or "(unnamed)"
        scopes = block.get("scopeSpans") or block.get("scope_spans") or []
        for scope in scopes if isinstance(scopes, list) else []:
            if not isinstance(scope, dict):
                continue
            spans = scope.get("spans") or []
            for span in spans if isinstance(spans, list) else []:
                if not isinstance(span, dict):
                    continue
                spans_read += 1
                status = span.get("status") if isinstance(span.get("status"), dict) else {}
                if status.get("code") not in _STATUS_ERROR:
                    continue
                error_spans += 1
                trace_id = _name(span.get("traceId") or span.get("trace_id"))
                if trace_id:
                    error_traces.add(trace_id)
                key = (service, _name(span.get("name")))
                row = groups.setdefault(
                    key,
                    {"service": key[0], "operation": key[1], "span_count": 0, "trace_ids": []},
                )
                row["span_count"] += 1
                if trace_id and trace_id not in row["trace_ids"]:
                    row["trace_ids"] = [*row["trace_ids"], trace_id][:_MAX_TRACE_CITATIONS]
    ranked = sorted(
        groups.values(),
        key=lambda row: (-row["span_count"], row["service"], row["operation"]),
    )
    return {
        "spans_read": spans_read,
        "error_spans": error_spans,
        "error_traces": len(error_traces),
        "error_groups": len(groups),
        "errors": ranked[:_MAX_ERROR_GROUPS],
    }
