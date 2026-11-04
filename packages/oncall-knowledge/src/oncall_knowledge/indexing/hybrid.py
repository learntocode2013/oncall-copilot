"""Reciprocal Rank Fusion.

RRF consumes ranks and ignores scores entirely. That is the point rather than a
simplification: a BM25 score of 5.8 and a cosine similarity of 0.71 are on
incomparable scales, and blending them needs a weight nobody in this project
could justify. A rank is a rank.

    RRF(d) = sum over rankings r of  1 / (k0 + rank_r(d))

k0 = 60 is the conventional default from the original paper. It is recorded as a
default, not as a finding -- nothing here measured it.
"""

from __future__ import annotations

from collections.abc import Sequence

DEFAULT_K0 = 60


def rrf(
    rankings: Sequence[Sequence[str]],
    *,
    k0: int = DEFAULT_K0,
) -> list[tuple[str, float]]:
    """Fuse ranked id lists into one, best first, ties broken by id.

    Takes the UNION of the inputs, never the intersection. The lexical half
    returns only documents that matched, while the semantic half always returns
    its top k, so almost every real fusion is over ragged lists -- and a
    document present in one list but not the other is exactly the case where one
    half found something the other missed. Those are the whole reason to fuse.
    """
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, document_id in enumerate(ranking, start=1):
            fused[document_id] = fused.get(document_id, 0.0) + 1.0 / (k0 + rank)

    return sorted(fused.items(), key=lambda pair: (-pair[1], pair[0]))
