"""Canonical schema for the On-Call Copilot runbook corpus.

Runbook metadata is stored as YAML front matter in Markdown files under
data/runbooks/.

Keep this module independent of file I/O so the schema can be reused by
the validator, ingestion pipeline, retrieval layer, and tests.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, get_args

from oncall_contracts.config import Severity

# Values currently used by the corpus and its policies.
RUNBOOK_KINDS = frozenset({"symptom", "cause"})

EVIDENCE_STATUSES = frozenset(
    {
        "verified",
        "structural",
        "hypothesis",
    }
)

# Derived from the contract, never restated. oncall_contracts.config.Severity is
# what IncidentState actually carries, so a second list here would drift.
SEVERITIES = frozenset(get_args(Severity))

# TODO: source these from oncall_contracts.tools once the MCP tool schemas land,
# so the validator checks against the same definitions ops-actions serves rather
# than a copy. Until then this list is the copy, and it will drift.
ACTION_NAMES = frozenset(
    {
        "set_flag",
        NO_ACTION_NAME := "none",
    }
)


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """A typed operation declared by a runbook.

    Example front matter:

        fix:
          action: set_flag
          args:
            flag: "paymentFailure"
            variant: "off"
    """

    action: str
    args: Mapping[str, Any] = field(default_factory=dict)

    # Required when action is "none": why remediation is inappropriate.
    reason: str | None = None

    @property
    def is_executable(self) -> bool:
        """False for the documented no-op, so callers branch on a field."""
        return self.action != NO_ACTION_NAME

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ActionSpec:
        """Build an ActionSpec from already-parsed YAML data."""
        action = value.get("action")
        if not isinstance(action, str):
            # ValueError throughout this module, not TypeError: the validator
            # catches ValueError to turn a bad document into a reported issue.
            raise ValueError("'action' must be a string")  # noqa: TRY004

        reason = value.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise ValueError("'reason' must be a string when present")

        if action == NO_ACTION_NAME:
            # The documented no-op: a flood is not a fault, and remediating a
            # healthy system is itself a harmful action. It takes no args and
            # must say why.
            if value.get("args"):
                raise ValueError(f"action {NO_ACTION_NAME!r} must not take args")
            if not reason or not reason.strip():
                raise ValueError(f"action {NO_ACTION_NAME!r} must carry a 'reason'")
            return cls(action=action, args={}, reason=reason.strip())

        args = value.get("args")
        if not isinstance(args, Mapping):
            raise ValueError("'args' must be a mapping")  # noqa: TRY004

        return cls(action=action, args=dict(args), reason=reason)


@dataclass(frozen=True, slots=True)
class RunbookMetadata:
    """Validated, typed front matter for one runbook."""

    kind: str
    status: str
    service: str
    symptom: str
    severity: str

    # Cause runbooks normally have a fault_id. Symptom runbooks need not.
    fault_id: str | None = None

    # Always a structured action. `action: "none"` is the documented no-op.
    fix: ActionSpec | None = None
    rollback: ActionSpec | None = None

    preconditions: tuple[str, ...] = ()
    queries: tuple[str, ...] = ()

    # Symptom runbooks only: the cause fault_ids this symptom narrows to.
    candidates: tuple[str, ...] = ()

    # Preserve fields that future consumers may need, without silently
    # dropping information that is not yet represented by this model.
    extra: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RunbookDocument:
    """A runbook's metadata plus its Markdown body."""

    document_id: str
    source_path: str
    metadata: RunbookMetadata
    body: str


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One actionable corpus validation finding."""

    path: str
    message: str
    field: str | None = None
    severity: str = "error"

    def __str__(self) -> str:
        location = self.path
        if self.field:
            location += f" [{self.field}]"
        return f"{location}: {self.severity}: {self.message}"


def parse_action(value: Any, field_name: str) -> ActionSpec | None:
    """Parse an action field without turning prose into executable commands."""
    if value is None:
        return None

    if not isinstance(value, Mapping):
        raise ValueError(  # noqa: TRY004
            f"{field_name} must be a structured action mapping or null; "
            f"a no-op is expressed as 'action: {NO_ACTION_NAME}' with a reason"
        )

    try:
        return ActionSpec.from_mapping(value)
    except ValueError as exc:
        raise ValueError(f"{field_name}: {exc}") from exc


def metadata_from_mapping(
    raw: Mapping[str, Any],
) -> RunbookMetadata:
    """Convert raw front matter into the canonical metadata model.

    This function checks basic types. Corpus-wide and flagd-dependent checks
    belong in validator.py.
    """

    def required_string(name: str) -> str:
        value = raw.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name!r} must be a non-empty string")
        return value.strip()

    def string_list(name: str) -> tuple[str, ...]:
        value = raw.get(name, [])
        if not isinstance(value, list):
            raise ValueError(f"{name!r} must be a list of strings")  # noqa: TRY004
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError(f"{name!r} must contain only non-empty strings")
        return tuple(item.strip() for item in value)

    kind = required_string("kind")
    status = required_string("status")
    service = required_string("service")
    symptom = required_string("symptom")
    severity = required_string("severity")

    fault_id = raw.get("fault_id")
    if fault_id is not None and (not isinstance(fault_id, str) or not fault_id.strip()):
        raise ValueError("'fault_id' must be a non-empty string when present")

    fix = parse_action(raw.get("fix"), "fix")
    rollback_value = parse_action(raw.get("rollback"), "rollback")

    known_fields = {
        "kind",
        "fault_id",
        "status",
        "service",
        "symptom",
        "severity",
        "fix",
        "rollback",
        "preconditions",
        "queries",
        "candidates",
    }

    return RunbookMetadata(
        kind=kind,
        status=status,
        service=service,
        symptom=symptom,
        severity=severity,
        fault_id=fault_id.strip() if isinstance(fault_id, str) else None,
        fix=fix,
        rollback=rollback_value,
        preconditions=string_list("preconditions"),
        queries=string_list("queries"),
        candidates=string_list("candidates"),
        extra={key: value for key, value in raw.items() if key not in known_fields},
    )
