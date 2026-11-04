from urllib.parse import parse_qs, urlparse

import pytest
from oncall_eval.signals import (
    PollPolicy,
    PrometheusClient,
    SignalPoller,
    SignalReadError,
    SignalTimeout,
)


def _scalar(value: str) -> dict:
    return {"status": "success", "data": {"resultType": "scalar", "result": [1, value]}}


def _vector(*values: str) -> dict:
    result = [{"metric": {}, "value": [1, value]} for value in values]
    return {"status": "success", "data": {"resultType": "vector", "result": result}}


def test_prometheus_client_encodes_query_and_reads_scalar():
    requested = []

    def fetch(url: str, timeout: float) -> dict:
        requested.append((url, timeout))
        return _scalar("0.75")

    client = PrometheusClient("http://prometheus:9090/", fetch_json=fetch)

    assert client.sample('sum(rate(requests_total{status="failed"}[5m]))') == 0.75
    assert parse_qs(urlparse(requested[0][0]).query)["query"] == [
        'sum(rate(requests_total{status="failed"}[5m]))'
    ]
    assert requested[0][1] == 10.0


def test_prometheus_client_reads_one_vector_and_treats_empty_or_nan_as_missing():
    payloads = iter([_vector("1.0"), _vector(), _scalar("NaN")])
    client = PrometheusClient("http://prometheus:9090", fetch_json=lambda *_: next(payloads))

    assert client.sample("up") == 1.0
    assert client.sample("up") is None
    assert client.sample("up") is None


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (_vector("1", "2"), "exactly one series"),
        ({"status": "error", "error": "bad query"}, "bad query"),
        (
            {"status": "success", "data": {"resultType": "matrix", "result": []}},
            "unsupported",
        ),
    ],
)
def test_prometheus_client_rejects_ambiguous_or_failed_response(payload: dict, message: str):
    client = PrometheusClient("http://prometheus:9090", fetch_json=lambda *_: payload)

    with pytest.raises(SignalReadError, match=message):
        client.sample("up")


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class SequenceSource:
    def __init__(self, values):
        self.values = iter(values)

    def sample(self, _query: str) -> float | None:
        value = next(self.values)
        if isinstance(value, Exception):
            raise value
        return value


def test_poller_requires_consecutive_successes_and_tolerates_transient_reads():
    clock = FakeClock()
    source = SequenceSource([SignalReadError("warming up"), 0.7, 0.2, 0.8, 0.9])
    poller = SignalPoller(
        source,
        PollPolicy(timeout_seconds=10, interval_seconds=1, consecutive_successes=2),
        clock=clock,
        sleep=clock.sleep,
    )

    observation = poller.wait(
        {"query": "failure_ratio", "operator": ">=", "threshold": 0.5},
        phase="detection",
    )

    assert observation.value == 0.9
    assert observation.attempts == 5
    assert observation.elapsed_seconds == 4.0


def test_poller_times_out_with_last_observation():
    clock = FakeClock()
    poller = SignalPoller(
        SequenceSource([0.1, 0.2, 0.3, 0.4]),
        PollPolicy(timeout_seconds=3, interval_seconds=1, consecutive_successes=1),
        clock=clock,
        sleep=clock.sleep,
    )

    with pytest.raises(SignalTimeout, match=r"last value: 0\.4"):
        poller.wait(
            {"query": "failure_ratio", "operator": ">=", "threshold": 0.5},
            phase="detection",
        )
