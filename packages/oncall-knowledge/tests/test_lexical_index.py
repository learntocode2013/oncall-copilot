"""Task 2: BM25 over the runbook corpus.

Two of these assertions exist because of measurements rather than taste.

Ties must break deterministically. Seven documents tie at 0.0 on a query that
matches nothing, and if their order comes from dict insertion then every
before/after delta in experiment E is partly noise.

`search` returns only documents the query actually matched. Exactly one of the
71 labelled queries scores 0.0 against its own runbook, and it already sits at
rank 13, so dropping zero-score results costs 0.000 Recall@5 -- while keeping
them would feed RRF an alphabetical ordering of seven unmatched documents as if
it were evidence.
"""

import pytest
from oncall_knowledge.indexing.lexical import LexicalIndex
from oncall_knowledge.ingestion import ingest_runbooks


@pytest.fixture(scope="module")
def documents():
    return ingest_runbooks()


@pytest.fixture(scope="module")
def index(documents):
    return LexicalIndex.build(documents)


def test_exact_identifier_query_finds_its_runbook(index):
    """`emailMemoryLeak` deliberately, not `paymentFailure`.

    Measured: emailMemoryLeak leads its runner-up by 13.72, while paymentFailure
    leads paymentUnreachable by 0.0413 -- thin enough to flip on an IDF
    implementation change, which would make this test about rank-bm25's internals
    rather than about our tokenizer.
    """
    results = index.search("emailMemoryLeak", k=1)

    assert results[0][0] == "email-memory-leak"


def test_the_payment_pair_is_near_tied_and_that_is_expected(index):
    """paymentFailure and paymentUnreachable discuss each other by design.

    Telling them apart is the diagnosis agent's job, not retrieval's. Retrieval
    only has to put both in front of it.
    """
    top_two = {doc_id for doc_id, _ in index.search("paymentFailure", k=2)}

    assert top_two == {"payment-failure", "payment-unreachable"}


def test_scores_come_back_descending(index):
    scores = [score for _, score in index.search("checkout 422 payment", k=5)]

    assert scores == sorted(scores, reverse=True)


def test_search_respects_k(index):
    assert len(index.search("payment", k=3)) <= 3


def test_ties_break_deterministically(documents):
    """Seven documents tie at 0.0 on an unmatched query. Insertion order there
    makes every A/B delta partly noise."""
    first = LexicalIndex.build(documents).search("checkout 422", k=5)
    second = LexicalIndex.build(documents).search("checkout 422", k=5)

    assert first == second


def test_empty_query_returns_no_results(index):
    assert index.search("", k=5) == []
    assert index.search("   \n\t ", k=5) == []


def test_a_query_matching_nothing_returns_no_results(index):
    """A zero-score document was not retrieved; it was merely not excluded."""
    assert index.search("zzzz qqqq xyzzy", k=5) == []


def test_entries_carry_status_and_source_path(index, documents):
    """status is carried for the consumer to judge (D13) but never indexed, and
    source_path is what the data-tree guard asserts on."""
    assert len(index.entries) == len(documents)
    assert {entry.status for entry in index.entries} <= {
        "verified",
        "structural",
        "hypothesis",
    }
    assert all(entry.source_path for entry in index.entries)


def test_status_is_not_searchable(index):
    """`status: structural` must not make "structural" a query term that pulls
    14 of 18 runbooks. The word appears in no runbook body."""
    assert index.search("structural", k=5) == []
