"""The one interface the incident commander calls.

This is the only public surface of oncall-knowledge (CONTRIBUTING.md).
Everything under `indexing/` is internal and may be replaced, which is what
makes "in-memory index, no vector database" (D10) a deferral rather than a
commitment.

`semantic=False` is config A and is exactly the committed baseline;
`semantic=True` is config B, BM25 and MiniLM fused by RRF.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from oncall_knowledge.indexing.hybrid import DEFAULT_K0, rrf
from oncall_knowledge.indexing.lexical import LexicalIndex
from oncall_knowledge.schema import RunbookDocument

#: Candidates taken from each half before fusion. Deeper than any reported k so
#: RRF has material to reorder, shallow enough to stay instant on 18 documents.
FUSION_DEPTH = 20


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """One hit, with enough provenance to argue about it.

    `lexical_rank` and `semantic_rank` are the pre-fusion positions, None when
    that half did not return the document. Without them an RRF hit cannot be
    attributed, and an unattributable hit cannot be debugged: there is no way to
    tell agreement between the halves from one half being confident alone.

    `status` is carried, never acted on (D13). The commander decides how much to
    trust a `structural` runbook; retrieval does not decide for it.
    """

    document_id: str
    status: str
    score: float
    lexical_rank: int | None
    semantic_rank: int | None


class Retriever:
    def __init__(
        self,
        lexical: LexicalIndex,
        semantic: object | None,
        *,
        k0: int = DEFAULT_K0,
    ) -> None:
        self._lexical = lexical
        self._semantic = semantic
        self._k0 = k0
        self._status = {entry.document_id: entry.status for entry in lexical.entries}

    @classmethod
    def build(
        cls,
        documents: Sequence[RunbookDocument],
        *,
        semantic: bool,
        k0: int = DEFAULT_K0,
    ) -> Retriever:
        """`semantic=True` imports and loads the model; False never touches it.

        The import sits inside the branch deliberately: config A must stay
        runnable with no torch installed, which is how CI scores the baseline.

        `k0` defaults to the published 60 and config B ships that value. It is
        exposed because it is NOT a neutral default on a corpus this small:
        measured over the 71 dev queries, Recall@5 is 0.972 at k0=60, 0.986 at
        20, and 1.000 at 1, while Recall@1 holds at 0.746 throughout. At 60 the
        gap between rank 1 and rank 20 is only about 25%, so a document found by
        both halves outranks one the semantic half placed first -- and the
        asymmetric finds are exactly what the semantic half was added for.

        It stays at 60 for the graded run because choosing it by its dev-set
        score would fit a parameter to the queries we report. Tune it on dev,
        report it on P4's set (D4).
        """
        lexical = LexicalIndex.build(documents)
        if not semantic:
            return cls(lexical, None, k0=k0)

        from oncall_knowledge.indexing.semantic import SemanticIndex

        return cls(lexical, SemanticIndex.build(documents), k0=k0)

    def retrieve(self, query: str, k: int = 5) -> list[RetrievalResult]:
        if self._semantic is None:
            # Config A: the lexical ranking, unfused. Putting one list through
            # RRF would reorder nothing, but it would make the baseline depend
            # on fusion code -- and the committed 0.972 record did not.
            return [
                RetrievalResult(
                    document_id=document_id,
                    status=self._status[document_id],
                    score=score,
                    lexical_rank=rank,
                    semantic_rank=None,
                )
                for rank, (document_id, score) in enumerate(self._lexical.search(query, k), start=1)
            ]

        lexical_ranking = [doc for doc, _ in self._lexical.search(query, FUSION_DEPTH)]
        semantic_ranking = [doc for doc, _ in self._semantic.search(query, FUSION_DEPTH)]
        lexical_positions = {doc: i for i, doc in enumerate(lexical_ranking, start=1)}
        semantic_positions = {doc: i for i, doc in enumerate(semantic_ranking, start=1)}

        return [
            RetrievalResult(
                document_id=document_id,
                status=self._status[document_id],
                score=score,
                lexical_rank=lexical_positions.get(document_id),
                semantic_rank=semantic_positions.get(document_id),
            )
            for document_id, score in rrf([lexical_ranking, semantic_ranking], k0=self._k0)[:k]
        ]

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        """The plain callable `evaluate()` takes.

        One evaluate() scores both configs through one interface, so an A/B
        comparison differs in the retriever and nothing else.
        """
        return [(result.document_id, result.score) for result in self.retrieve(query, k)]
