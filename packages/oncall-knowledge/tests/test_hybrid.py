"""Task 6: Reciprocal Rank Fusion.

RRF consumes ranks and ignores scores, which is the whole point: BM25 scores and
cosine similarities are on incomparable scales, and any attempt to blend them
needs a weight nobody can justify. A rank is a rank.

The target is known before writing a line. Measured on the 71 dev queries:

    lexical          Recall@5 0.972   Recall@1 0.648   MRR 0.787
    semantic         Recall@5 0.958   Recall@1 0.732   MRR 0.827
    either one finds it           1.000

So fusion has a real ceiling to aim at, and the two halves are complementary
rather than redundant -- semantic rescues 'assistant feels sluggish but works'
(lexical misses it entirely), lexical holds 'consumer lag is growing' (semantic
drops it to rank 12).
"""

import pytest
from oncall_knowledge.indexing.hybrid import rrf


def test_rrf_uses_ranks_not_scores():
    """Two rankings that agree on 'b' put it first, even though it is never
    rank 1 in either."""
    fused = [document_id for document_id, _ in rrf([["a", "b"], ["c", "b"]])]

    assert fused[0] == "b"


def test_rrf_matches_the_formula():
    """1 / (k0 + rank), k0 = 60, summed across rankings."""
    fused = dict(rrf([["a", "b"]], k0=60))

    assert fused["a"] == pytest.approx(1 / 61)
    assert fused["b"] == pytest.approx(1 / 62)


def test_contributions_from_both_rankings_add():
    fused = dict(rrf([["a"], ["a"]], k0=60))

    assert fused["a"] == pytest.approx(2 / 61)


def test_a_document_in_only_one_ranking_still_appears():
    """Lexical returns only matches, semantic always returns its top k, so most
    fusions are over ragged lists. Dropping the asymmetric tail would discard
    exactly the documents one half found and the other did not."""
    fused = dict(rrf([["a"], ["b"]]))

    assert set(fused) == {"a", "b"}


def test_results_are_sorted_by_fused_score_then_id():
    """Ties must not depend on dict insertion order, or an A/B delta is partly
    noise."""
    first = rrf([["a", "b"], ["b", "a"]])
    second = rrf([["b", "a"], ["a", "b"]])

    assert [d for d, _ in first] == [d for d, _ in second]


def test_empty_rankings_fuse_to_nothing():
    assert rrf([]) == []
    assert rrf([[], []]) == []


def test_one_empty_ranking_does_not_discard_the_other():
    """An empty query to the lexical half returns [], and that must not erase
    the semantic half's opinion."""
    fused = dict(rrf([[], ["a", "b"]]))

    assert set(fused) == {"a", "b"}


def test_k0_damps_the_difference_between_adjacent_ranks():
    """Larger k0 flattens; the conventional 60 is a default, not a finding."""
    tight = dict(rrf([["a", "b"]], k0=1))
    flat = dict(rrf([["a", "b"]], k0=1000))

    assert tight["a"] - tight["b"] > flat["a"] - flat["b"]
