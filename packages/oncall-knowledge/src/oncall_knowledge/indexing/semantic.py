"""The embedding half of config B.

A missing `sentence-transformers` raises rather than falling back to lexical
scoring. A fallback would make config B score IDENTICALLY to config A, so the
experiment would report "no improvement from hybrid retrieval" when the truth is
"the semantic half never ran" -- and with Recall@5 saturated at 0.972, those two
conclusions are indistinguishable from the number alone.

Note the deliberate asymmetry with LexicalIndex: BM25 drops zero-score
documents, because a zero there means "contains none of the query terms". Cosine
similarity over L2-normalised vectors has no such value, so this index always
returns its top k. RRF consumes ranks rather than scores, so both feed it fine.
"""

from __future__ import annotations

from collections.abc import Sequence

from oncall_knowledge.indexing.lexical import IndexEntry
from oncall_knowledge.indexing.text import index_text, tokenize
from oncall_knowledge.schema import RunbookDocument


class SemanticUnavailableError(RuntimeError):
    """The optional extra is not installed.

    Deliberately NOT an ImportError subclass: `except ImportError` is exactly
    how an optional dependency turns into a silent fallback, and someone will
    write it.
    """


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity, since both vectors arrive L2-normalised.

    Written out rather than using numpy: 18 documents times 384 dimensions is
    not worth importing an array library this module would otherwise not need.
    """
    return float(sum(a * b for a, b in zip(left, right, strict=True)))


class SemanticIndex:
    MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"

    # Pin the exact commit. A moving reference like "main" would change the
    # ruler between run A and run B. Get the value with the extra installed:
    #   uv run python -c "from huggingface_hub import model_info; \
    #       print(model_info('sentence-transformers/all-MiniLM-L6-v2').sha)"
    MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"

    def __init__(
        self,
        entries: Sequence[IndexEntry],
        vectors: Sequence[Sequence[float]],
        model: object,
    ) -> None:
        self._entries = tuple(entries)
        self._vectors = tuple(vectors)
        self._model = model

    @staticmethod
    def _load_model() -> object:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise SemanticUnavailableError(
                "the semantic index needs sentence-transformers, which is an optional "
                "extra: run `uv sync --extra semantic`. It is not installed by default "
                "because it pulls torch (~2 GB) into every CI job."
            ) from exc

        return SentenceTransformer(SemanticIndex.MODEL_ID, revision=SemanticIndex.MODEL_REVISION)

    @classmethod
    def build(cls, documents: Sequence[RunbookDocument]) -> SemanticIndex:
        model = cls._load_model()
        entries = [
            IndexEntry(
                document_id=document.document_id,
                text=index_text(document),
                status=document.metadata.status,
                source_path=document.source_path,
            )
            for document in documents
        ]
        vectors = model.encode([entry.text for entry in entries], normalize_embeddings=True)
        return cls(entries, vectors, model)

    @property
    def entries(self) -> tuple[IndexEntry, ...]:
        return self._entries

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        """Top k by cosine similarity, ties broken by `document_id`.

        The emptiness check goes through `tokenize` so the contract matches
        LexicalIndex exactly: whitespace-only is not a query.
        """
        if not tokenize(query):
            return []

        vector = self._model.encode([query], normalize_embeddings=True)[0]
        scored = [
            (entry.document_id, _dot(vector, document_vector))
            for entry, document_vector in zip(self._entries, self._vectors, strict=True)
        ]
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return scored[:k]
