"""The store's job: hand over approved lessons, and nothing else, ever."""

from __future__ import annotations

from pathlib import Path

import pytest
from oncall_memory import LessonStore, load_postmortems

CORPUS = """---
kind: postmortem
incident_id: inc-1
title: a payment thing
service: payment
symptom: checkout error rate
lessons:
  - id: les-approved
    status: approved
    statement: read the flag baseline first
    preconditions:
      service: payment
      symptom: checkout error rate
  - id: les-unreviewed
    status: inferred
    statement: a force-recreate fixes it
    preconditions:
      service: payment
      symptom: checkout error rate
  - id: les-refused
    status: rejected
    statement: unhealthy but up means something changed
    reason: it sends you the wrong way when nothing changed
  - id: les-replaced
    status: superseded
    statement: an older form of the first lesson
    preconditions:
      service: payment
---

body
"""

OTHER = """---
kind: postmortem
incident_id: inc-2
title: an ad thing
service: ad
symptom: cpu saturation
lessons:
  - id: les-other-service
    status: approved
    statement: read the cpu against the working set
    preconditions:
      service: ad
      symptom: cpu saturation
---

body
"""


def build(tmp_path: Path, *documents: str) -> LessonStore:
    for index, text in enumerate(documents):
        (tmp_path / f"{index}.md").write_text(text, encoding="utf-8")
    return LessonStore.from_postmortems(tmp_path)


def test_only_approved_lessons_are_handed_over(tmp_path: Path) -> None:
    """The one invariant. `inferred` is a candidate no person has reviewed;
    `rejected` was refused; `superseded` was replaced. None is guidance."""
    store = build(tmp_path, CORPUS)

    assert len(store) == 4
    assert [
        row["id"] for row in store.search(service="payment", symptom="checkout error rate")
    ] == ["les-approved"]


def test_a_missing_status_filter_means_approved_not_everything(tmp_path: Path) -> None:
    """Fail closed. The permissive reading of `status=None` would put an
    unreviewed lesson in a brief, which is the whole failure being prevented."""
    store = build(tmp_path, CORPUS)

    assert [row["id"] for row in store.search(status=None)] == ["les-approved"]


def test_every_row_carries_an_explicit_status(tmp_path: Path) -> None:
    """`partition_lessons` defaults a missing status to "approved" -- fail-open.
    Nothing this store emits can reach that branch."""
    store = build(tmp_path, CORPUS, OTHER)

    for row in store.search() + store.rejected():
        assert row["status"], f"{row['id']} has no explicit status"


def test_the_store_does_not_narrow_on_preconditions(tmp_path: Path) -> None:
    """Deliberate, and the reason is demo scene 3.

    The scene shows a lesson REJECTED because its preconditions do not match, and
    it renders the dropped list `partition_lessons` returns. A store that
    narrowed first would hand over nothing to reject and the scene would become
    an empty list, with no error anywhere. So a payment incident is still offered
    the `ad` lesson, and the graph is what drops it.
    """
    store = build(tmp_path, CORPUS, OTHER)

    offered = [row["id"] for row in store.search(service="payment", symptom="checkout error rate")]
    assert offered == ["les-approved", "les-other-service"]


def test_rejected_lessons_are_available_to_a_person_with_the_reason(tmp_path: Path) -> None:
    store = build(tmp_path, CORPUS)

    (refused,) = store.rejected()
    assert refused["id"] == "les-refused"
    assert refused["reason"]


def test_asking_search_for_a_non_applicable_status_raises(tmp_path: Path) -> None:
    """`search` is the path a brief is built from, so no argument may route a
    refused or unreviewed lesson into one -- not even an explicit one. Returning
    them for `status="rejected"` would be a filter working exactly as asked and
    a guardrail gone."""
    store = build(tmp_path, CORPUS)

    for status in ("rejected", "inferred", "superseded"):
        with pytest.raises(ValueError, match="guidance"):
            store.search(status=status)

    for status in ("approved", None, ""):
        assert "les-refused" not in [row["id"] for row in store.search(status=status)]


def test_the_playbook_hash_changes_when_the_playbook_does(tmp_path: Path) -> None:
    """What "frozen before the held-out run" means, in a form that can be checked."""
    before = build(tmp_path, CORPUS).playbook_hash
    after = build(tmp_path, CORPUS, OTHER).playbook_hash

    assert before != after
    assert len(before) == 64


def test_the_playbook_hash_ignores_what_the_agent_cannot_apply(tmp_path: Path) -> None:
    """Editing a rejected lesson's reason must not invalidate a comparison
    between two runs: the agent never saw it, so the runs applied the same
    guidance."""
    first = build(tmp_path, CORPUS).playbook_hash
    edited = build(
        tmp_path, CORPUS.replace("it sends you the wrong way when nothing changed", "reworded")
    ).playbook_hash

    assert first == edited


def test_the_hash_is_stable_across_file_order(tmp_path: Path) -> None:
    """Two machines that globbed in a different order must agree."""
    one = build(tmp_path, CORPUS, OTHER).playbook_hash
    two = build(tmp_path, OTHER, CORPUS).playbook_hash

    assert one == two


def test_the_committed_seed_corpus_loads(tmp_path: Path) -> None:
    """The real one, not a fixture. The seeds are data a PR reviewer reads, so a
    typo in front matter must fail here and not at demo time."""
    store = LessonStore.from_postmortems()

    assert len(store.postmortems) >= 3
    assert store.search(service="payment", symptom="checkout error rate")
    assert store.rejected(), "the playbook must keep one rejected lesson and its reason"
    assert load_postmortems() == store.postmortems
