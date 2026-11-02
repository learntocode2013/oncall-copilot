"""Run one evaluation case, score it, and persist its durable RunRecord."""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, get_args

from oncall_contracts.config import Topology
from oncall_contracts.faults import FaultSpec
from oncall_contracts.runrecord import RunRecord
from oncall_contracts.state import Alert

from .cases import EvalCase, validate_case
from .injector import FlagdFaultInjector
from .lifecycle import ObservedFault, observed_fault
from .scoring import Score, SystemRun, score_run
from .signals import SignalPoller


class SystemUnderTest(Protocol):
    """Boundary later implemented by the incident API client."""

    def run(
        self,
        alert: Alert,
        *,
        incident_id: str,
        config_id: Topology,
        tool_budget: int,
    ) -> SystemRun: ...


@dataclass(frozen=True)
class EvaluationResult:
    record: RunRecord
    score: Score
    artifact_path: Path
    observation: ObservedFault | None


class EvaluationRunner:
    """Orchestrate the private harness without importing the production graph."""

    def __init__(
        self,
        *,
        injector: FlagdFaultInjector,
        poller: SignalPoller,
        system: SystemUnderTest,
        artifacts_dir: Path,
        clock: Callable[[], float] = time.monotonic,
        id_factory: Callable[[], str] | None = None,
    ):
        self.injector = injector
        self.poller = poller
        self.system = system
        self.artifacts_dir = artifacts_dir
        self.clock = clock
        self.id_factory = id_factory or (lambda: uuid.uuid4().hex)

    def run(self, case: EvalCase, fault: FaultSpec, config_id: Topology) -> EvaluationResult:
        """Execute and persist one case, including failures as records."""
        if problems := validate_case(case, {fault["fault_id"]: fault}):
            raise ValueError("invalid evaluation case: " + "; ".join(problems))
        if config_id not in get_args(Topology):
            raise ValueError(f"unknown topology {config_id!r}")

        run_id = self.id_factory()
        if not run_id or run_id in {".", ".."} or Path(run_id).name != run_id:
            raise ValueError("run id must be a non-empty filename-safe value")
        incident_id = f"eval-{run_id}"
        output: SystemRun | None = None
        observation: ObservedFault | None = None
        latency_ms = 0
        harness_error: str | None = None

        try:
            with observed_fault(self.injector, self.poller, fault) as observed:
                observation = observed
                started = self.clock()
                try:
                    output = self.system.run(
                        case["alert"],
                        incident_id=incident_id,
                        config_id=config_id,
                        tool_budget=case["expected"]["max_tool_calls"],
                    )
                finally:
                    latency_ms = max(0, round((self.clock() - started) * 1000))
        except Exception as exc:  # noqa: BLE001 - failures must become durable run records
            harness_error = _format_error(exc)

        if output is None:
            output = _empty_system_run(harness_error or "system returned no result")
        elif harness_error:
            output = dict(output)
            output["error"] = _join_errors(output.get("error"), harness_error)

        try:
            _validate_system_run(output)
            score = score_run(case, output)
            record = _record(
                run_id=run_id,
                case=case,
                config_id=config_id,
                output=output,
                score=score,
                latency_ms=latency_ms,
            )
        except (KeyError, TypeError, ValueError) as exc:
            error = _join_errors(harness_error, f"invalid system result: {exc}")
            output = _empty_system_run(error)
            score = score_run(case, output)
            record = _record(
                run_id=run_id,
                case=case,
                config_id=config_id,
                output=output,
                score=score,
                latency_ms=latency_ms,
            )

        artifact_path = self.artifacts_dir / f"{run_id}.json"
        write_run_record(artifact_path, record)
        return EvaluationResult(
            record=record,
            score=score,
            artifact_path=artifact_path,
            observation=observation,
        )


def _validate_system_run(run: SystemRun) -> None:
    required = {
        "root_cause_services",
        "proposed_action",
        "executed_actions",
        "tool_calls",
        "tokens",
        "usd",
        "verified_recovery",
        "rolled_back",
        "model_ids",
    }
    if missing := required - set(run):
        raise ValueError(f"missing fields {sorted(missing)}")
    if unknown := set(run) - set(SystemRun.__annotations__):
        raise ValueError(f"unknown fields {sorted(unknown)}")
    for field in ("tool_calls", "tokens"):
        value = run[field]
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{field} must be an integer")
        if value < 0:
            raise ValueError(f"{field} must be a non-negative integer")
    if isinstance(run["usd"], bool) or not isinstance(run["usd"], (int, float)):
        raise TypeError("usd must be a number")
    if not math.isfinite(run["usd"]) or run["usd"] < 0:
        raise ValueError("usd must be finite and non-negative")
    if not isinstance(run["verified_recovery"], bool) or not isinstance(run["rolled_back"], bool):
        raise TypeError("verified_recovery and rolled_back must be booleans")
    if not isinstance(run["root_cause_services"], list):
        raise TypeError("root_cause_services must be a list")
    if any(not isinstance(service, str) for service in run["root_cause_services"]):
        raise TypeError("root_cause_services entries must be strings")
    if not isinstance(run["executed_actions"], list):
        raise TypeError("executed_actions must be a list")
    for action in run["executed_actions"]:
        _validate_action(action, "executed_actions entry")
    if run["proposed_action"] is not None:
        _validate_action(run["proposed_action"], "proposed_action")
    if not isinstance(run["model_ids"], dict):
        raise TypeError("model_ids must be an object")
    if any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in run["model_ids"].items()
    ):
        raise TypeError("model_ids keys and values must be strings")
    if run.get("playbook_hash") is not None and not isinstance(run["playbook_hash"], str):
        raise TypeError("playbook_hash must be a string or null")
    if run.get("error") is not None and not isinstance(run["error"], str):
        raise TypeError("error must be a string or null")


def _validate_action(action: object, field: str) -> None:
    if not isinstance(action, dict):
        raise TypeError(f"{field} must be an object")
    if set(action) != {"action", "args"}:
        raise ValueError(f"{field} must contain exactly 'action' and 'args'")
    if not isinstance(action["action"], str) or not action["action"]:
        raise TypeError(f"{field}.action must be a non-empty string")
    if not isinstance(action["args"], dict):
        raise TypeError(f"{field}.args must be an object")


def _record(
    *,
    run_id: str,
    case: EvalCase,
    config_id: Topology,
    output: SystemRun,
    score: Score,
    latency_ms: int,
) -> RunRecord:
    record: RunRecord = {
        "run_id": run_id,
        "case_id": case["case_id"],
        "fault_id": case["fault_id"],
        "config_id": config_id,
        "complexity": case["complexity"],
        "tool_calls": output["tool_calls"],
        "tokens": output["tokens"],
        "usd": float(output["usd"]),
        "latency_ms": latency_ms,
        "correct_root_cause": score.correct_root_cause,
        "root_cause_rank": score.root_cause_rank,
        "correct_action_proposed": score.correct_action_proposed,
        "verified_recovery": output["verified_recovery"],
        "harmful_actions": score.harmful_actions,
        "rolled_back": output["rolled_back"],
        "model_ids": dict(output["model_ids"]),
        "playbook_hash": output.get("playbook_hash"),
    }
    if output.get("error"):
        record["error"] = output["error"]
    return record


def _empty_system_run(error: str) -> SystemRun:
    return {
        "root_cause_services": [],
        "proposed_action": None,
        "executed_actions": [],
        "tool_calls": 0,
        "tokens": 0,
        "usd": 0.0,
        "verified_recovery": False,
        "rolled_back": False,
        "model_ids": {},
        "playbook_hash": None,
        "error": error,
    }


def _join_errors(first: str | None, second: str) -> str:
    return f"{first}; {second}" if first else second


def _format_error(exc: BaseException) -> str:
    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(_format_error(item) for item in exc.exceptions)
    return f"{type(exc).__name__}: {exc}"


def write_run_record(path: Path, record: RunRecord) -> None:
    """Atomically persist one record; dashboards never observe partial JSON."""
    if problems := validate_run_record(record):
        raise ValueError("invalid run record: " + "; ".join(problems))
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(record, indent=2, sort_keys=True) + "\n").encode()
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def load_run_record(path: Path) -> RunRecord:
    """Load a persisted record for reporting and mock-gate tests."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        # File loaders expose malformed syntax and malformed shape uniformly.
        raise ValueError("run record must be a JSON object")  # noqa: TRY004
    if problems := validate_run_record(value):
        raise ValueError("invalid run record: " + "; ".join(problems))
    return value  # type: ignore[return-value]


def validate_run_record(value: object) -> list[str]:
    """Return contract violations suitable for CLI and fixture diagnostics."""
    if not isinstance(value, dict):
        return ["run record must be an object"]
    problems = []
    allowed = set(RunRecord.__annotations__)
    required = set(RunRecord.__required_keys__)
    if missing := required - set(value):
        problems.append(f"missing fields {sorted(missing)}")
    if unknown := set(value) - allowed:
        problems.append(f"unknown fields {sorted(unknown)}")

    if value.get("config_id") not in get_args(Topology):
        problems.append(f"unknown config_id {value.get('config_id')!r}")
    if value.get("complexity") not in {"single", "multi"}:
        problems.append(f"unknown complexity {value.get('complexity')!r}")
    for field in ("tool_calls", "tokens", "latency_ms", "harmful_actions"):
        item = value.get(field)
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            problems.append(f"{field} must be a non-negative integer")
    usd = value.get("usd")
    if (
        isinstance(usd, bool)
        or not isinstance(usd, (int, float))
        or not math.isfinite(usd)
        or usd < 0
    ):
        problems.append("usd must be finite and non-negative")
    for field in (
        "correct_root_cause",
        "correct_action_proposed",
        "verified_recovery",
        "rolled_back",
    ):
        if not isinstance(value.get(field), bool):
            problems.append(f"{field} must be a boolean")
    rank = value.get("root_cause_rank")
    if rank is not None and (isinstance(rank, bool) or not isinstance(rank, int) or rank <= 0):
        problems.append("root_cause_rank must be a positive integer or null")
    models = value.get("model_ids")
    if not isinstance(models, dict) or any(
        not isinstance(key, str) or not isinstance(model, str) for key, model in models.items()
    ):
        problems.append("model_ids must map strings to strings")
    for field in ("playbook_hash", "error"):
        if value.get(field) is not None and not isinstance(value.get(field), str):
            problems.append(f"{field} must be a string or null")
    return problems
