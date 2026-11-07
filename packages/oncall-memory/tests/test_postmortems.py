"""The loader's job is to refuse a corpus that would read as guidance it is not."""

from __future__ import annotations

from pathlib import Path

import pytest
from oncall_memory import LESSON_STATUSES, PostmortemError, load_postmortems

APPROVED = """---
kind: postmortem
incident_id: inc-1
title: a thing happened
service: payment
symptom: checkout error rate
lessons:
  - id: les-1
    status: approved
    statement: read the flag baseline first
    preconditions:
      service: payment
      symptom: checkout error rate
---

body
"""


def write(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def test_a_valid_postmortem_yields_its_lessons(tmp_path: Path) -> None:
    write(tmp_path, "a.md", APPROVED)
    (postmortem,) = load_postmortems(tmp_path)

    assert postmortem.incident_id == "inc-1"
    assert [lesson.lesson_id for lesson in postmortem.lessons] == ["les-1"]
    # Defaults to the postmortem's own incident, so forget() has a reference
    # that resolves without the author restating it.
    assert postmortem.lessons[0].derived_from == "inc-1"


def test_an_approved_lesson_must_state_a_precondition(tmp_path: Path) -> None:
    """An empty precondition matches EVERY incident.

    `precondition_matches` returns True for `{}`, so an approved lesson with no
    preconditions is guidance that applies to everything -- the trap
    docs/memory-policy.md works through with ADR-0001 and ADR-0002.
    """
    write(
        tmp_path,
        "a.md",
        APPROVED.replace(
            """    preconditions:
      service: payment
      symptom: checkout error rate
""",
            "",
        ),
    )

    with pytest.raises(PostmortemError) as caught:
        load_postmortems(tmp_path)
    assert "at least one precondition" in str(caught.value)


def test_a_precondition_nobody_evaluates_is_refused(tmp_path: Path) -> None:
    """Stated and unchecked is worse than absent.

    `precondition_matches` reads `service` and `symptom` only. A lesson guarded
    by `change_precedes_alert` would be applied to every incident while appearing
    to be guarded, which is the failure this whole project keeps meeting.
    """
    write(
        tmp_path,
        "a.md",
        APPROVED.replace(
            "      symptom: checkout error rate\n",
            "      symptom: checkout error rate\n      change_precedes_alert: 'true'\n",
        ),
    )

    with pytest.raises(PostmortemError) as caught:
        load_postmortems(tmp_path)
    message = str(caught.value)
    assert "never evaluated" in message
    assert "change_precedes_alert" in message


def test_a_rejected_lesson_must_keep_its_reason(tmp_path: Path) -> None:
    """A refusal with no reason is indistinguishable from an oversight."""
    write(tmp_path, "a.md", APPROVED.replace("status: approved", "status: rejected"))

    with pytest.raises(PostmortemError) as caught:
        load_postmortems(tmp_path)
    assert "must keep the reason" in str(caught.value)


def test_an_unknown_status_is_refused(tmp_path: Path) -> None:
    write(tmp_path, "a.md", APPROVED.replace("status: approved", "status: probably-fine"))

    with pytest.raises(PostmortemError) as caught:
        load_postmortems(tmp_path)
    assert "status must be one of" in str(caught.value)
    assert sorted(LESSON_STATUSES) == ["approved", "inferred", "rejected", "superseded"]


def test_a_runbook_cannot_drift_into_the_postmortem_corpus(tmp_path: Path) -> None:
    """`kind` is checked because the two corpora have disjoint vocabularies: a
    runbook's status is verified/structural/hypothesis, a lesson's is about human
    review. A runbook read as a postmortem would contribute no lessons and raise
    nothing."""
    write(tmp_path, "a.md", APPROVED.replace("kind: postmortem", "kind: cause"))

    with pytest.raises(PostmortemError) as caught:
        load_postmortems(tmp_path)
    assert "kind must be 'postmortem'" in str(caught.value)


def test_a_duplicate_lesson_id_is_refused(tmp_path: Path) -> None:
    """A duplicate id makes the playbook hash depend on file order."""
    write(tmp_path, "a.md", APPROVED)
    write(tmp_path, "b.md", APPROVED.replace("inc-1", "inc-2"))

    with pytest.raises(PostmortemError) as caught:
        load_postmortems(tmp_path)
    assert "already used by" in str(caught.value)


def test_a_lesson_derived_from_nothing_is_refused(tmp_path: Path) -> None:
    """A lesson that outlives the incident justifying it is unfalsifiable, and
    `forget(incident_id)` can only cascade along a reference that resolves."""
    write(
        tmp_path,
        "a.md",
        APPROVED.replace(
            "    status: approved\n", "    status: approved\n    derived_from: inc-does-not-exist\n"
        ),
    )

    with pytest.raises(PostmortemError) as caught:
        load_postmortems(tmp_path)
    assert "names no postmortem in this corpus" in str(caught.value)


def test_every_issue_is_reported_at_once(tmp_path: Path) -> None:
    """A seed corpus is authored in batches. One exception per run is how the
    runbook corpus got written twice."""
    write(tmp_path, "a.md", APPROVED.replace("status: approved", "status: rejected"))
    write(
        tmp_path,
        "b.md",
        APPROVED.replace("inc-1", "inc-2").replace("kind: postmortem", "kind: cause"),
    )

    with pytest.raises(PostmortemError) as caught:
        load_postmortems(tmp_path)
    assert len(caught.value.issues) >= 2


def test_an_empty_directory_is_not_a_failure(tmp_path: Path) -> None:
    """A fresh clone has no postmortems, and oncall-service must still start."""
    assert load_postmortems(tmp_path) == ()


def test_a_missing_directory_is_a_failure(tmp_path: Path) -> None:
    """The opposite of the above: nothing to read is normal, the wrong path is not.
    A silently empty playbook is a memory-off run reported as memory-on."""
    with pytest.raises(PostmortemError) as caught:
        load_postmortems(tmp_path / "nope")
    assert "does not exist" in str(caught.value)
