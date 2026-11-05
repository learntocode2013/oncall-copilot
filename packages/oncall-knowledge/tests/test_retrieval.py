"""Task 6: the one interface the incident commander calls.

`Retriever` is the only public surface of oncall-knowledge (CONTRIBUTING.md).
Everything under `indexing/` is internal and may be replaced -- which is what
makes "in-memory index, no vector database" (D10) a deferral rather than a
commitment.

Two requirements here are not about retrieval quality at all:

Results carry each retriever's own rank, because an RRF hit you cannot
attribute is an RRF hit you cannot debug -- you cannot tell whether it came
from the lexical half, the semantic half, or agreement between them.

Retrieval never filters or re-ranks on `status` (D13). 14 of 18 runbooks are
`structural` and 2 are `hypothesis`; hiding them would bake a trust policy into
the index and make a retrieval regression indistinguishable from a policy
change.
"""

import pytest
from oncall_knowledge.ingestion import ingest_runbooks
from oncall_knowledge.retrieval import RetrievalResult, Retriever


@pytest.fixture(scope="module")
def documents():
    return ingest_runbooks()


@pytest.fixture(scope="module")
def lexical_only(documents):
    """Config A: the baseline, no model needed."""
    return Retriever.build(documents, semantic=False)


# --- the result shape ----------------------------------------------------


def test_results_are_typed_and_carry_status(lexical_only):
    results = lexical_only.retrieve("paymentFailure")

    assert all(isinstance(result, RetrievalResult) for result in results)
    assert results[0].status in {"verified", "structural", "hypothesis"}


def test_results_expose_each_retriever_rank(lexical_only):
    """With no semantic half, semantic_rank must be None rather than 0 or absent."""
    top = lexical_only.retrieve("emailMemoryLeak")[0]

    assert top.document_id == "email-memory-leak"
    assert top.lexical_rank == 1
    assert top.semantic_rank is None


def test_retrieve_respects_k(lexical_only):
    assert len(lexical_only.retrieve("payment", k=3)) <= 3


def test_an_empty_query_retrieves_nothing(lexical_only):
    assert lexical_only.retrieve("") == []
    assert lexical_only.retrieve("   \n\t ") == []


def test_scores_come_back_descending(lexical_only):
    scores = [result.score for result in lexical_only.retrieve("checkout 422", k=5)]

    assert scores == sorted(scores, reverse=True)


# --- the search callable evaluate() consumes -----------------------------


def test_search_is_the_plain_callable_evaluate_takes(lexical_only):
    """One evaluate() must score config A and config B through one interface,
    or the comparison is between two measurement paths as well as two configs.
    """
    results = lexical_only.search("paymentFailure", 5)

    assert all(isinstance(pair, tuple) and len(pair) == 2 for pair in results)
    assert results[0][0] == "payment-failure"


def test_config_a_matches_the_bare_lexical_index(documents):
    """Retriever(semantic=False) must BE the baseline, not an approximation of
    it. The committed 0.972 record was produced by LexicalIndex directly."""
    from oncall_knowledge.indexing.lexical import LexicalIndex

    index = LexicalIndex.build(documents)
    retriever = Retriever.build(documents, semantic=False)

    for query in ("paymentFailure", "checkout 422", "consumer lag is growing"):
        assert [d for d, _ in retriever.search(query, 5)] == [d for d, _ in index.search(query, 5)]


# --- D13: status is exposed, never acted on ------------------------------


def test_retrieval_does_not_filter_on_status(documents, lexical_only):
    """Every `hypothesis` runbook must be reachable by its own queries."""
    for document in (d for d in documents if d.metadata.status == "hypothesis"):
        reachable = {
            result.document_id
            for query in document.metadata.queries
            for result in lexical_only.retrieve(query, k=5)
        }

        assert document.document_id in reachable, f"{document.document_id} is unreachable"


# --- the hybrid ----------------------------------------------------------


def test_the_hybrid_attributes_a_hit_to_both_halves(documents):
    pytest.importorskip("sentence_transformers")
    retriever = Retriever.build(documents, semantic=True)

    top = retriever.retrieve("paymentFailure")[0]

    assert top.lexical_rank is not None
    assert top.semantic_rank is not None


def test_at_the_default_k0_fusion_loses_a_semantic_only_find(documents):
    """RRF rewards agreement over conviction, and k0=60 makes that severe.

    'assistant feels sluggish but works' shares zero vocabulary with its
    runbook. Semantic ranks ai-slow-response FIRST; lexical does not return it
    at all, so it earns one contribution of 1/(60+1) = 0.0164 while eight
    documents found by both halves earn two contributions each, 0.029 to 0.032.
    Agreement at rank 9 beats conviction at rank 1.

    This is the limitation, asserted rather than wished away. Measured on the 71
    dev queries: Recall@5 is 0.972 at k0=60, 0.986 at 20, 1.000 at 1 -- while
    Recall@1 holds at 0.746 throughout. k0=60 ships because tuning it on these
    queries would fit a parameter to the ruler we report (D4).
    """
    pytest.importorskip("sentence_transformers")
    retriever = Retriever.build(documents, semantic=True)

    top = [r.document_id for r in retriever.retrieve("assistant feels sluggish but works", k=5)]

    assert "ai-slow-response" not in top


def test_a_small_k0_restores_the_semantic_only_find(documents):
    """The same query, the same indexes, k0=1: conviction now outweighs
    agreement. This is what makes k0 a parameter worth choosing on P4's set
    rather than a constant worth forgetting."""
    pytest.importorskip("sentence_transformers")
    retriever = Retriever.build(documents, semantic=True, k0=1)

    top = [r.document_id for r in retriever.retrieve("assistant feels sluggish but works", k=5)]

    assert "ai-slow-response" in top


def test_the_hybrid_keeps_what_only_the_lexical_half_finds(documents):
    """Semantic drops this to rank 12; a distinctive term beats a blurred one."""
    pytest.importorskip("sentence_transformers")
    retriever = Retriever.build(documents, semantic=True)

    top = [r.document_id for r in retriever.retrieve("consumer lag is growing", k=5)]

    assert "kafka-queue-problems" in top


def test_a_hit_found_by_only_one_half_says_so(documents):
    """One rank present, the other None -- the attribution §11 asked for.

    Uses k0=1 so the semantic-only find actually reaches the top 5; at the
    default it does not, which the test above records.
    """
    pytest.importorskip("sentence_transformers")
    retriever = Retriever.build(documents, semantic=True, k0=1)

    results = retriever.retrieve("assistant feels sluggish but works", k=5)
    found = next(r for r in results if r.document_id == "ai-slow-response")

    assert found.semantic_rank is not None
    assert found.lexical_rank is None
