"""Read `data/postmortems/*.md` into postmortems and the lessons they justify.

A lesson is not an observation. It is a generalisation, and `docs/memory-policy.md`
says it enters the playbook only through a person. That makes the committed
postmortem the lesson's SOURCE and its justification at once: the lesson lives
inside the incident that produced it, so `derived_from` points at something real
and a later `forget(incident_id)` has something to cascade to.

WHY THESE FILES ARE NOT RUNBOOKS, AND NOT VALIDATED AS RUNBOOKS. The corpus
vocabulary is `RUNBOOK_KINDS = {symptom, cause}` with
`EVIDENCE_STATUSES = {verified, structural, hypothesis}`. A lesson's statuses are
`inferred`, `approved`, `rejected`, `superseded` -- a disjoint set, because they
answer a different question: not "how well is this known?" but "has a person
accepted it?". `docs/memory-policy.md` claimed these were "the same vocabulary as
the corpus"; they never were, and that sentence is corrected in the same commit
as this file.

WHY THESE FILES ARE NOT IN THE RETRIEVAL INDEX. `ingest_runbooks` is pinned to
`DEFAULT_RUNBOOK_DIR` and raises on any other directory, so adding documents here
cannot change Recall@5. That is deliberate: `oncall_knowledge.experiment` refuses
to compare two retrieval runs whose corpus size differs, and indexing these
documents is exactly that event. When they do join the index it must be all of
them at once, strictly before the first scored run or strictly after the last.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from oncall_knowledge.schema import ValidationIssue

#: Has a person accepted this? NOT the corpus's evidence vocabulary -- see the
#: module docstring.
LESSON_STATUSES = frozenset({"inferred", "approved", "rejected", "superseded"})

#: The only status the agent may be shown as guidance. Everything else is either
#: unreviewed (`inferred`), refused (`rejected`) or replaced (`superseded`).
APPLICABLE_STATUS = "approved"

#: The precondition keys `oncall_graph.briefs.precondition_matches` actually
#: evaluates. It reads `service` and `symptom` and nothing else, so any other key
#: is a precondition that LOOKS checked and is not -- the precise failure this
#: project keeps finding. Authoring one is refused here rather than ignored at
#: match time. Widening this set means widening `precondition_matches` first,
#: which is P2's file.
EVALUABLE_PRECONDITIONS = ("service", "symptom")

DEFAULT_POSTMORTEM_DIR = Path(__file__).resolve().parents[4] / "data" / "postmortems"

_KIND = "postmortem"
_FRONT_MATTER = "---"


class PostmortemError(Exception):
    """The postmortem corpus could not be read. Carries every issue, not the first.

    One message per problem, because a seed corpus is authored in batches and
    fixing them one exception at a time is how the runbook corpus got written
    twice.
    """

    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        super().__init__(
            "the postmortem corpus is invalid:\n  " + "\n  ".join(str(i) for i in issues)
        )


@dataclass(frozen=True, slots=True)
class Lesson:
    """One generalisation, and everything code needs to refuse to apply it."""

    lesson_id: str
    statement: str
    status: str
    service: str
    preconditions: dict[str, str]
    derived_from: str
    evidence_refs: tuple[str, ...] = ()
    #: Required when `status` is `rejected`. The policy keeps a rejected lesson
    #: WITH its reason: it is a counterexample, and a refusal with no reason is
    #: indistinguishable from an oversight.
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        """The shape `oncall_graph.handoffs.partition_lessons` reads.

        `status` is always present and always explicit. `partition_lessons`
        defaults a missing status to "approved" -- fail-open -- so a lesson that
        lost its status in transit would be applied as reviewed guidance. Nothing
        this store emits can take that branch.
        """
        return {
            "id": self.lesson_id,
            "statement": self.statement,
            "status": self.status,
            "service": self.service,
            "preconditions": dict(self.preconditions),
            "evidence_refs": list(self.evidence_refs),
            "derived_from": self.derived_from,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class Postmortem:
    """One incident, written up, with the lessons it justified."""

    incident_id: str
    title: str
    service: str
    symptom: str
    path: str
    body: str
    lessons: tuple[Lesson, ...] = field(default_factory=tuple)


def _split_front_matter(text: str, rel: str, issues: list[ValidationIssue]) -> dict[str, Any]:
    if not text.startswith(_FRONT_MATTER):
        issues.append(ValidationIssue(rel, "no YAML front matter"))
        return {}
    parts = text.split(_FRONT_MATTER, 2)
    if len(parts) < 3:
        issues.append(ValidationIssue(rel, "front matter is not closed with ---"))
        return {}
    try:
        loaded = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        issues.append(ValidationIssue(rel, f"front matter is not valid YAML: {exc}"))
        return {}
    if not isinstance(loaded, dict):
        issues.append(ValidationIssue(rel, "front matter must be a mapping"))
        return {}
    loaded["__body__"] = parts[2].strip()
    return loaded


def _text(meta: dict[str, Any], key: str, rel: str, issues: list[ValidationIssue]) -> str:
    value = meta.get(key)
    if not isinstance(value, str) or not value.strip():
        issues.append(
            ValidationIssue(rel, f"{key} is required and must be a non-empty string", key)
        )
        return ""
    return value.strip()


def _preconditions(
    raw: Any, *, rel: str, lesson_id: str, status: str, issues: list[ValidationIssue]
) -> dict[str, str]:
    """Parse preconditions, and refuse an approved lesson that states none.

    `precondition_matches` treats an empty mapping as matching EVERYTHING, so an
    approved lesson with no preconditions is guidance that applies to every
    incident. `docs/memory-policy.md` has the worked example: "when a service is
    unhealthy but up, check recent flag and config changes first" is right for
    ADR-0001 and sends you entirely the wrong way on ADR-0002, and the
    precondition that separates them is the one the lesson failed to state.
    """
    where = f"lesson {lesson_id}"
    if raw is None:
        clean: dict[str, str] = {}
    elif isinstance(raw, dict):
        clean = {str(k): str(v).strip() for k, v in raw.items() if str(v).strip()}
    else:
        issues.append(
            ValidationIssue(rel, f"{where}: preconditions must be a mapping", "preconditions")
        )
        return {}
    unknown = sorted(set(clean) - set(EVALUABLE_PRECONDITIONS))
    if unknown:
        issues.append(
            ValidationIssue(
                rel,
                f"{where}: precondition(s) {unknown!r} are never evaluated; "
                f"precondition_matches reads only {list(EVALUABLE_PRECONDITIONS)!r}. "
                "A precondition that is stated and not checked is worse than none.",
                "preconditions",
            )
        )
    if status == APPLICABLE_STATUS and not clean:
        issues.append(
            ValidationIssue(
                rel,
                f"{where}: an approved lesson must state at least one precondition; "
                "an empty precondition matches every incident",
                "preconditions",
            )
        )
    return clean


def _lesson(
    raw: Any, *, rel: str, incident_id: str, service: str, issues: list[ValidationIssue]
) -> Lesson | None:
    if not isinstance(raw, dict):
        issues.append(ValidationIssue(rel, "each entry in lessons must be a mapping", "lessons"))
        return None

    lesson_id = str(raw.get("id") or "").strip()
    if not lesson_id:
        issues.append(ValidationIssue(rel, "a lesson needs an id", "lessons"))
        return None

    status = str(raw.get("status") or "").strip()
    if status not in LESSON_STATUSES:
        issues.append(
            ValidationIssue(
                rel,
                f"lesson {lesson_id}: status must be one of {sorted(LESSON_STATUSES)!r}, "
                f"not {status!r}",
                "status",
            )
        )
        return None

    statement = str(raw.get("statement") or "").strip()
    if not statement:
        issues.append(
            ValidationIssue(rel, f"lesson {lesson_id}: statement is required", "statement")
        )
        return None

    reason = str(raw.get("reason") or "").strip()
    if status == "rejected" and not reason:
        issues.append(
            ValidationIssue(
                rel,
                f"lesson {lesson_id}: a rejected lesson must keep the reason it was refused",
                "reason",
            )
        )
        return None

    refs = raw.get("evidence_refs") or []
    if not isinstance(refs, list):
        issues.append(ValidationIssue(rel, f"lesson {lesson_id}: evidence_refs must be a list"))
        return None

    return Lesson(
        lesson_id=lesson_id,
        statement=statement,
        status=status,
        service=str(raw.get("service") or service).strip(),
        preconditions=_preconditions(
            raw.get("preconditions"), rel=rel, lesson_id=lesson_id, status=status, issues=issues
        ),
        # Defaults to this postmortem's own incident. A lesson that names another
        # incident is allowed and checked below: forget(incident_id) can only
        # cascade along a reference that resolves.
        derived_from=str(raw.get("derived_from") or incident_id).strip(),
        evidence_refs=tuple(str(r) for r in refs),
        reason=reason,
    )


def _postmortem(path: Path, root: Path, issues: list[ValidationIssue]) -> Postmortem | None:
    rel = (
        str(path.relative_to(root.parent.parent))
        if root.parent.parent in path.parents
        else path.name
    )
    meta = _split_front_matter(path.read_text(encoding="utf-8"), rel, issues)
    if not meta:
        return None

    kind = str(meta.get("kind") or "").strip()
    if kind != _KIND:
        issues.append(ValidationIssue(rel, f"kind must be {_KIND!r}, not {kind!r}", "kind"))
        return None

    incident_id = _text(meta, "incident_id", rel, issues)
    title = _text(meta, "title", rel, issues)
    service = _text(meta, "service", rel, issues)
    symptom = _text(meta, "symptom", rel, issues)
    if not (incident_id and title and service and symptom):
        return None

    raw_lessons = meta.get("lessons") or []
    if not isinstance(raw_lessons, list):
        issues.append(ValidationIssue(rel, "lessons must be a list", "lessons"))
        return None

    lessons = [
        lesson
        for lesson in (
            _lesson(raw, rel=rel, incident_id=incident_id, service=service, issues=issues)
            for raw in raw_lessons
        )
        if lesson is not None
    ]
    return Postmortem(
        incident_id=incident_id,
        title=title,
        service=service,
        symptom=symptom,
        path=rel,
        body=str(meta.get("__body__") or ""),
        lessons=tuple(lessons),
    )


def load_postmortems(directory: Path | None = None) -> tuple[Postmortem, ...]:
    """Every postmortem in `directory`, validated as a set.

    Raises `PostmortemError` listing every issue. An empty directory is NOT an
    error: the store is legitimately empty before any postmortem is authored, and
    a hard failure there would make `oncall-service` unstartable on a fresh clone.
    """
    root = (directory or DEFAULT_POSTMORTEM_DIR).resolve()
    issues: list[ValidationIssue] = []
    if not root.is_dir():
        raise PostmortemError([ValidationIssue(str(root), "postmortem directory does not exist")])

    found = []
    for path in sorted(root.glob("*.md")):
        postmortem = _postmortem(path, root, issues)
        if postmortem is not None:
            found.append(postmortem)

    _check_uniqueness(found, issues)
    if issues:
        raise PostmortemError(issues)
    return tuple(found)


def _check_uniqueness(found: list[Postmortem], issues: list[ValidationIssue]) -> None:
    """Ids must be unique, and `derived_from` must resolve.

    A duplicate lesson id makes the playbook hash depend on file order, and a
    `derived_from` pointing at no incident is a lesson that cannot be forgotten
    with the incident that justified it -- which the policy calls unfalsifiable.
    """
    incidents: dict[str, str] = {}
    for postmortem in found:
        if postmortem.incident_id in incidents:
            issues.append(
                ValidationIssue(
                    postmortem.path,
                    f"incident_id {postmortem.incident_id!r} is already used by "
                    f"{incidents[postmortem.incident_id]}",
                    "incident_id",
                )
            )
        incidents[postmortem.incident_id] = postmortem.path

    seen: dict[str, str] = {}
    for postmortem in found:
        for lesson in postmortem.lessons:
            if lesson.lesson_id in seen:
                issues.append(
                    ValidationIssue(
                        postmortem.path,
                        f"lesson id {lesson.lesson_id!r} is already used by {seen[lesson.lesson_id]}",
                        "lessons",
                    )
                )
            seen[lesson.lesson_id] = postmortem.path
            if lesson.derived_from not in incidents:
                issues.append(
                    ValidationIssue(
                        postmortem.path,
                        f"lesson {lesson.lesson_id}: derived_from {lesson.derived_from!r} "
                        "names no postmortem in this corpus",
                        "derived_from",
                    )
                )
