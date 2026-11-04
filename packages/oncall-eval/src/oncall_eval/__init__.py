"""P4 — fault injection, evaluation and red-team tooling.

This package is a leaf: no production package imports it, and it drives the
system over HTTP rather than importing the graph it measures.
"""

from .cases import load_case, validate_case
from .http_client import IncidentApiClient, IncidentApiError
from .injector import ActiveFault, FaultInjectionError, FlagdFaultInjector
from .lifecycle import ObservedFault, observed_fault
from .manifest import load_manifest
from .runner import (
    EvaluationResult,
    EvaluationRunner,
    SystemUnderTest,
    load_run_record,
    validate_run_record,
)
from .scoring import Score, SystemRun, score_run
from .signals import (
    Observation,
    PollPolicy,
    PrometheusClient,
    SignalPoller,
    SignalReadError,
    SignalTimeout,
)

__all__ = [
    "ActiveFault",
    "EvaluationResult",
    "EvaluationRunner",
    "FaultInjectionError",
    "FlagdFaultInjector",
    "IncidentApiClient",
    "IncidentApiError",
    "Observation",
    "ObservedFault",
    "PollPolicy",
    "PrometheusClient",
    "Score",
    "SignalPoller",
    "SignalReadError",
    "SignalTimeout",
    "SystemRun",
    "SystemUnderTest",
    "load_case",
    "load_manifest",
    "load_run_record",
    "observed_fault",
    "score_run",
    "validate_case",
    "validate_run_record",
]
