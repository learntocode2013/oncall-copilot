"""Task 5: the embedding half of config B.

The most important test here has nothing to do with embeddings. If
`sentence-transformers` is missing and the index quietly falls back to lexical
scoring, config B scores IDENTICALLY to config A and the experiment reports "no
improvement from hybrid retrieval" when the truth is "the semantic half never
ran". With Recall@5 already saturated at 0.972, those two conclusions are
indistinguishable from the number alone. So it raises.

Note the deliberate asymmetry with LexicalIndex: BM25 drops zero-score
documents, because a zero there means "contains none of the query terms".
Cosine similarity over L2-normalised vectors has no such value -- every
document has some similarity to every query -- so the semantic index always
returns its top k. RRF consumes ranks, not scores, so the two feed it fine.
"""

import sys

import pytest
from oncall_knowledge.indexing.semantic import SemanticIndex, SemanticUnavailableError
from oncall_knowledge.ingestion import ingest_runbooks


@pytest.fixture(scope="module")
def documents():
    return ingest_runbooks()


# --- the failure that would look like a result ---------------------------


def test_missing_dependency_raises_instead_of_falling_back(monkeypatch, documents):
    """A silent fallback would make config B score exactly like config A."""
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)

    with pytest.raises(SemanticUnavailableError, match="uv sync --extra semantic"):
        SemanticIndex.build(documents)


def test_the_error_is_not_an_importerror(monkeypatch, documents):
    """Callers must not be able to swallow this with `except ImportError`, which
    is exactly how an optional dependency becomes a silent fallback."""
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)

    with pytest.raises(SemanticUnavailableError):
        SemanticIndex.build(documents)

    assert not issubclass(SemanticUnavailableError, ImportError)


# --- the pin -------------------------------------------------------------


def test_the_model_is_pinned():
    assert SemanticIndex.MODEL_ID == "sentence-transformers/all-MiniLM-L6-v2"
    assert SemanticIndex.MODEL_REVISION


def test_the_revision_is_not_a_moving_reference():
    """`main` would silently change the ruler between run A and run B -- the
    same failure the query-set hash exists to catch, in a different costume."""
    assert SemanticIndex.MODEL_REVISION not in {"main", "master", "latest", "HEAD", ""}


# --- behaviour, once the extra is installed ------------------------------


def test_finds_the_paraphrase_bm25_misses(documents):
    """'assistant feels sluggish but works' shares ZERO vocabulary with its
    runbook and sits at BM25 rank 13 of 18. It is the single clearest case for
    embeddings in this corpus, and the one query that justifies config B.
    """
    pytest.importorskip("sentence_transformers")
    index = SemanticIndex.build(documents)

    top = [doc_id for doc_id, _ in index.search("assistant feels sluggish but works", k=5)]

    assert "ai-slow-response" in top


def test_exact_identifier_still_works(documents):
    """Embeddings must not be WORSE than BM25 on the easy case."""
    pytest.importorskip("sentence_transformers")
    index = SemanticIndex.build(documents)

    top = [doc_id for doc_id, _ in index.search("emailMemoryLeak", k=3)]

    assert "email-memory-leak" in top


def test_empty_query_returns_no_results(documents):
    """Same contract as LexicalIndex: no query, no ranking."""
    pytest.importorskip("sentence_transformers")
    index = SemanticIndex.build(documents)

    assert index.search("", k=5) == []
    assert index.search("   \n\t ", k=5) == []


def test_scores_come_back_descending_and_respect_k(documents):
    pytest.importorskip("sentence_transformers")
    index = SemanticIndex.build(documents)

    results = index.search("checkout is failing", k=4)
    scores = [score for _, score in results]

    assert len(results) == 4
    assert scores == sorted(scores, reverse=True)


def test_search_is_deterministic(documents):
    """Two builds of the same corpus must rank identically, or every A/B delta
    carries embedding noise."""
    pytest.importorskip("sentence_transformers")

    first = SemanticIndex.build(documents).search("payment is broken", k=5)
    second = SemanticIndex.build(documents).search("payment is broken", k=5)

    assert first == second


def test_it_indexes_the_same_text_as_the_lexical_index(documents):
    """If the two halves read different text, config B changes two things at
    once and the before/after number attributes nothing.

    IndexEntry should move to indexing/text.py in Task 6, where the hybrid
    retriever needs it too -- at which point the query-leak guard should assert
    on the shared builder rather than on LexicalIndex alone.
    """
    pytest.importorskip("sentence_transformers")
    from oncall_knowledge.indexing.lexical import LexicalIndex

    assert SemanticIndex.build(documents).entries == LexicalIndex.build(documents).entries
