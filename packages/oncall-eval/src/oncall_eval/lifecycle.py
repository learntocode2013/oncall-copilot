"""Compose safe fault mutation with detection and recovery observations."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from oncall_contracts.faults import FaultSpec

from .injector import ActiveFault, FlagdFaultInjector
from .signals import Observation, SignalPoller


@dataclass
class ObservedFault:
    """Evidence collected while exercising one fault."""

    active: ActiveFault
    detection: Observation
    recovery: Observation | None = None


@contextmanager
def observed_fault(
    injector: FlagdFaultInjector,
    poller: SignalPoller,
    fault: FaultSpec,
) -> Iterator[ObservedFault]:
    """Yield only after detection; clean up and prove recovery on exit."""
    activated = False
    observed: ObservedFault | None = None
    failure: BaseException | None = None
    try:
        with injector.injected(fault) as active:
            activated = True
            detection = poller.wait(fault["detect"], phase="detection")
            observed = ObservedFault(active=active, detection=detection)
            yield observed
    except BaseException as exc:  # noqa: BLE001 - cleanup must also run for cancellation/interrupt
        failure = exc

    # ``FlagdFaultInjector.__exit__`` has run before this block, so recovery
    # can only pass after the disabled variant has been restored.
    if activated:
        try:
            recovery = poller.wait(fault["recovery"], phase="recovery")
            if observed is not None:
                observed.recovery = recovery
        except BaseException as recovery_failure:
            if failure is not None:
                raise BaseExceptionGroup(
                    "evaluation and recovery both failed", [failure, recovery_failure]
                ) from None
            raise

    if failure is not None:
        raise failure.with_traceback(failure.__traceback__)
