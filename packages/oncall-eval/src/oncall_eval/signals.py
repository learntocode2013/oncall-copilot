"""Prometheus signal sampling and bounded lifecycle polling."""

from __future__ import annotations

import json
import math
import operator
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from oncall_contracts.faults import FaultSignal, SignalOperator


class SignalReadError(RuntimeError):
    """Prometheus returned an unusable response."""


class SignalTimeout(TimeoutError):
    """A detection or recovery condition was not observed in time."""


JsonFetcher = Callable[[str, float], Any]


def _fetch_json(url: str, timeout_seconds: float) -> Any:
    try:
        with urlopen(url, timeout=timeout_seconds) as response:
            return json.load(response)
    except (OSError, TimeoutError, URLError, json.JSONDecodeError) as exc:
        raise SignalReadError(f"Prometheus request failed: {exc}") from exc


class PrometheusClient:
    """Minimal instant-query client kept outside the production graph package."""

    def __init__(
        self,
        base_url: str,
        *,
        request_timeout_seconds: float = 10.0,
        fetch_json: JsonFetcher | None = None,
    ):
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("Prometheus base_url must use http or https")
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        self.base_url = base_url.rstrip("/")
        self.request_timeout_seconds = request_timeout_seconds
        self.fetch_json = fetch_json or _fetch_json

    def sample(self, query: str) -> float | None:
        """Return one scalar sample, or ``None`` when the query has no data."""
        if not isinstance(query, str) or not query.strip():
            raise SignalReadError("Prometheus query must be a non-empty string")
        url = f"{self.base_url}/api/v1/query?{urlencode({'query': query})}"
        payload = self.fetch_json(url, self.request_timeout_seconds)
        if not isinstance(payload, Mapping):
            raise SignalReadError("Prometheus response must be an object")
        if payload.get("status") != "success":
            raise SignalReadError(
                f"Prometheus query failed: {payload.get('error') or 'unknown error'}"
            )
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise SignalReadError("Prometheus response has no data object")

        result_type = data.get("resultType")
        if result_type == "scalar":
            raw_sample = data.get("result")
        elif result_type == "vector":
            result = data.get("result")
            if not isinstance(result, list):
                raise SignalReadError("Prometheus vector result must be a list")
            if not result:
                return None
            if len(result) != 1:
                raise SignalReadError(
                    f"signal query must return exactly one series, returned {len(result)}"
                )
            item = result[0]
            if not isinstance(item, Mapping):
                raise SignalReadError("Prometheus vector entry must be an object")
            raw_sample = item.get("value")
        else:
            raise SignalReadError(f"unsupported Prometheus result type {result_type!r}")
        return _sample_value(raw_sample)


def _sample_value(raw_sample: Any) -> float | None:
    if not isinstance(raw_sample, list) or len(raw_sample) != 2:
        raise SignalReadError("Prometheus sample must be a [timestamp, value] pair")
    try:
        value = float(raw_sample[1])
    except (TypeError, ValueError) as exc:
        raise SignalReadError(f"Prometheus sample is not numeric: {raw_sample[1]!r}") from exc
    return value if math.isfinite(value) else None


class SignalSource(Protocol):
    def sample(self, query: str) -> float | None: ...


@dataclass(frozen=True)
class PollPolicy:
    timeout_seconds: float = 120.0
    interval_seconds: float = 5.0
    consecutive_successes: int = 2

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if self.consecutive_successes <= 0:
            raise ValueError("consecutive_successes must be positive")


@dataclass(frozen=True)
class Observation:
    phase: str
    value: float
    attempts: int
    elapsed_seconds: float


_OPERATORS: dict[SignalOperator, Callable[[float, float], bool]] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
}


class SignalPoller:
    """Wait for a signal condition without allowing an unbounded evaluation."""

    def __init__(
        self,
        source: SignalSource,
        policy: PollPolicy | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.source = source
        self.policy = policy or PollPolicy()
        self.clock = clock
        self.sleep = sleep

    def wait(self, signal: FaultSignal, *, phase: str) -> Observation:
        """Return after stable success or raise a diagnostic timeout."""
        start = self.clock()
        deadline = start + self.policy.timeout_seconds
        attempts = 0
        successes = 0
        last_value: float | None = None
        last_error: SignalReadError | None = None
        comparator = _OPERATORS[signal["operator"]]

        while True:
            attempts += 1
            try:
                last_value = self.source.sample(signal["query"])
                last_error = None
            except SignalReadError as exc:
                last_error = exc
                last_value = None

            if last_value is not None and comparator(last_value, signal["threshold"]):
                successes += 1
                if successes >= self.policy.consecutive_successes:
                    return Observation(
                        phase=phase,
                        value=last_value,
                        attempts=attempts,
                        elapsed_seconds=round(self.clock() - start, 3),
                    )
            else:
                successes = 0

            now = self.clock()
            if now >= deadline:
                detail = f"last error: {last_error}" if last_error else f"last value: {last_value}"
                raise SignalTimeout(
                    f"{phase} signal was not observed within {self.policy.timeout_seconds}s "
                    f"after {attempts} attempts ({detail})"
                )
            self.sleep(min(self.policy.interval_seconds, deadline - now))
