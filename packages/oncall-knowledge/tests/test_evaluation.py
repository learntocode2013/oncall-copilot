"""Task 3: the metrics, and the ruler they are computed against.

Gold is a SET, not a single document (D12). `ads fail to load on product pages`
matches ad-failure, ad-high-cpu and ad-manual-gc on the evidence a retriever
has; the symptom runbook names five candidate causes in its own front matter.
Scoring those as failures would understate retrieval and reward overfitting to
one arbitrary choice.

Gold labels are `document_id`s. For all 17 cause runbooks that is identical to
`fault_id`, so P4 can write either; the one symptom runbook has no `fault_id`
and can only be named by document_id.
"""

from pathlib import Path

import pytest
from oncall_knowledge.evaluation import (
    QueryCase,
    dev_cases,
    evaluate,
    load_cases,
    query_set_hash,
    rank_of_first_gold,
)
from oncall_knowledge.ingestion import ingest_runbooks

CASES = [
    QueryCase("q1", frozenset({"a"})),
    QueryCase("q2", frozenset({"b", "c"})),
]


def ranking(*document_ids: str):
    """A search callable that always returns the same ranking."""
    return lambda query, depth: [(doc_id, 1.0) for doc_id in document_ids][:depth]


# --- rank_of_first_gold --------------------------------------------------


def test_rank_is_one_based_and_none_for_a_miss():
    """None, never 0: a miss must not be falsy-indistinguishable from rank 1
    and must not arithmetically become a reciprocal of 1/0."""
    assert rank_of_first_gold(["x", "a"], frozenset({"a"})) == 2
    assert rank_of_first_gold(["x", "y"], frozenset({"a"})) is None
    assert rank_of_first_gold([], frozenset({"a"})) is None


def test_any_gold_member_counts_as_a_hit():
    assert rank_of_first_gold(["c", "b"], frozenset({"b", "c"})) == 1


def test_the_best_ranked_gold_member_wins():
    """Not the first member of the set, which has no order."""
    assert rank_of_first_gold(["x", "c", "b"], frozenset({"b", "c"})) == 2


# --- evaluate ------------------------------------------------------------


def test_metrics_on_a_known_ranking():
    def search(query, depth):
        return [("a", 1.0)] if query == "q1" else [("z", 1.0)]

    result = evaluate(search, CASES)

    assert result.recall_at_1 == 0.5
    assert result.recall_at_5 == 0.5
    assert result.mrr == 0.5
    assert result.n_cases == 2


def test_mrr_is_not_censored_at_five():
    """Recall@5 saturates on 18 documents, so MRR carries the signal. A gold at
    rank 8 must contribute 1/8 rather than 0, or MRR stops discriminating too.
    """
    case = [QueryCase("q", frozenset({"gold"}))]
    others = [f"d{i}" for i in range(7)]

    result = evaluate(ranking(*others, "gold"), case)

    assert result.recall_at_5 == 0.0
    assert result.mrr == pytest.approx(1 / 8)


def test_a_total_miss_contributes_zero_to_mrr():
    result = evaluate(ranking("x", "y"), [QueryCase("q", frozenset({"gold"}))])

    assert result.mrr == 0.0
    assert result.cases[0].rank is None


def test_per_case_results_are_kept():
    """A single aggregate number cannot support a claim about WHICH queries
    improved, which is the whole point of the before/after report."""
    result = evaluate(ranking("a"), CASES)

    assert len(result.cases) == 2
    assert result.cases[0].query == "q1"
    assert result.cases[0].gold == frozenset({"a"})
    assert result.cases[0].top


# --- the ruler -----------------------------------------------------------


def test_query_set_hash_is_order_independent():
    assert query_set_hash(CASES) == query_set_hash(list(reversed(CASES)))


def test_query_set_hash_changes_when_a_gold_label_changes():
    narrowed = [CASES[0], QueryCase("q2", frozenset({"b"}))]

    assert query_set_hash(CASES) != query_set_hash(narrowed)


def test_query_set_hash_changes_when_a_query_changes():
    reworded = [QueryCase("q1 reworded", frozenset({"a"})), CASES[1]]

    assert query_set_hash(CASES) != query_set_hash(reworded)


# --- the case loaders ----------------------------------------------------


def test_dev_cases_cover_every_labelled_query():
    documents = ingest_runbooks()

    cases = dev_cases(documents)

    assert len(cases) == sum(len(d.metadata.queries) for d in documents)


def test_dev_cases_gold_is_the_runbook_the_query_was_authored_in():
    documents = ingest_runbooks()
    payment = next(d for d in documents if d.document_id == "payment-failure")

    cases = [c for c in dev_cases(documents) if c.query in payment.metadata.queries]

    assert cases
    assert all(c.gold == frozenset({"payment-failure"}) for c in cases)


def test_load_cases_accepts_a_single_gold_or_a_list(tmp_path: Path):
    path = tmp_path / "queries.yaml"
    path.write_text(
        "queries:\n"
        "  - query: customers cannot pay\n"
        "    gold: payment-failure\n"
        "  - query: ads fail to load\n"
        "    gold: [ad-failure, ad-high-cpu]\n",
        encoding="utf-8",
    )

    cases = load_cases(path, known_ids={"payment-failure", "ad-failure", "ad-high-cpu"})

    assert cases[0].gold == frozenset({"payment-failure"})
    assert cases[1].gold == frozenset({"ad-failure", "ad-high-cpu"})


def test_load_cases_rejects_an_unknown_gold_label(tmp_path: Path):
    """A typo in P4's file would otherwise become an unscoreable case: it looks
    exactly like a retrieval miss and silently lowers the score for the wrong
    reason. P4 authors the labels and P1 cannot check them by reading.
    """
    path = tmp_path / "queries.yaml"
    path.write_text(
        "queries:\n  - query: customers cannot pay\n    gold: payment-failur\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="payment-failur"):
        load_cases(path, known_ids={"payment-failure"})


def test_load_cases_rejects_a_case_with_no_gold(tmp_path: Path):
    path = tmp_path / "queries.yaml"
    path.write_text("queries:\n  - query: customers cannot pay\n", encoding="utf-8")

    with pytest.raises(ValueError, match="gold"):
        load_cases(path, known_ids={"payment-failure"})
