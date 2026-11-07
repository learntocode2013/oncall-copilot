"""Demo scene 3, end to end: an old lesson rejected because its preconditions do not match.

This file lives in the root test tree on purpose. It imports BOTH `oncall_memory`
and `oncall_graph`, and `test_dependency_direction` scans whole package
directories including their tests -- so a test of the seam between two packages
cannot live inside either one.

What it proves is the mechanism, not the prose: the store hands over every
approved lesson, and P2's `partition_lessons` is what drops the ones whose
preconditions do not fit the live case. Both halves have to be present for the
scene to show anything.
"""

from __future__ import annotations

from oncall_graph.handoffs import load_lessons
from oncall_memory import APPLICABLE_STATUS, LessonStore

#: The alert the demo actually pages. `eval/faults/manifest.yaml` publishes the
#: checkout symptom for `payment-failure`, and the lab posts exactly this text.
CASE = {"service": "payment", "symptom": "checkout error rate above 5% for five minutes"}


def playbook() -> LessonStore:
    return LessonStore.from_postmortems()


def test_the_matching_lesson_is_kept_and_the_stale_ones_are_dropped() -> None:
    kept, dropped = load_lessons(playbook(), **CASE)

    assert [lesson["id"] for lesson in kept] == ["les-read-the-flag-baseline-first"]
    assert [lesson["id"] for lesson in dropped] == [
        "les-a-soft-limit-never-fails",
        "les-health-check-names-a-binary",
    ]


def test_a_dropped_lesson_carries_the_precondition_that_disqualified_it() -> None:
    """The scene has to be able to say WHY. A dropped list of bare ids shows a
    filter ran; it does not show the reasoning the objective asks us to
    demonstrate."""
    _, dropped = load_lessons(playbook(), **CASE)

    for lesson in dropped:
        assert lesson["preconditions"], f"{lesson['id']} was dropped with nothing to point at"
        assert lesson["preconditions"]["service"] != CASE["service"]


def test_nothing_unreviewed_or_refused_ever_reaches_the_graph() -> None:
    """The invariant, checked at the seam rather than inside the store.

    `partition_lessons` defaults a lesson with no status to "approved" and drops
    only the statuses it names, so an `inferred` lesson that reached it WOULD be
    kept and shown as reviewed guidance. The store is what prevents that, and
    this asserts the property rather than that quirk -- it keeps passing if P2
    makes the graph fail closed too.
    """
    kept, dropped = load_lessons(playbook(), **CASE)

    for lesson in kept + dropped:
        assert lesson["status"] == APPLICABLE_STATUS, (
            f"{lesson['id']} is {lesson['status']!r} and reached the graph"
        )


def test_the_rejected_lesson_is_a_deliverable_and_stays_out_of_the_run() -> None:
    """One rejected lesson with its reason is required by the project guide. It
    must be visible to a person and invisible to the agent."""
    store = playbook()
    (refused,) = store.rejected()
    kept, dropped = load_lessons(store, **CASE)

    assert refused["reason"]
    assert refused["id"] not in [lesson["id"] for lesson in kept + dropped]


def test_an_empty_playbook_drops_nothing_and_raises_nothing() -> None:
    """A memory-off arm of the comparison, and the shape a fresh clone has."""
    assert load_lessons(None, **CASE) == ([], [])
    assert load_lessons([], **CASE) == ([], [])
