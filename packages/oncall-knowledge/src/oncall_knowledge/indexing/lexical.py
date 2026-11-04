"""BM25 over the runbook corpus. Config A of experiment E.

The scoring function is ours rather than rank-bm25's, which reverses D7. The
reason D7 gave -- "scoring is not where this project's risk lives" -- turned out
to be false on a corpus this small. Measured over the 71 labelled queries:

    BM25Okapi (rank-bm25 default)   Recall@5 0.958   Recall@1 0.606
    BM25Plus  (rank-bm25)           Recall@5 0.972   Recall@1 0.648
    this file                       Recall@5 0.972   Recall@1 0.648

With 18 documents, 93 terms appear in 10 or more of them and so earn a NEGATIVE
Okapi IDF. rank-bm25 floors those to one positive constant, which makes every
common word contribute the same weight and costs 4 points of Recall@1.

BM25Plus recovers the number but adds a `delta` term that applies at zero term
frequency, so all 18 documents score nonzero for "payment" when only 6 contain
it -- which turns the zero-score filter below into a no-op and hands RRF an
ordering of 12 documents that do not contain the query term. This formula keeps
IDF positive and monotonic without that, so a zero score still means "no match".
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence

from oncall_knowledge.indexing.text import IndexEntry, build_entries, tokenize
from oncall_knowledge.schema import RunbookDocument

K1 = 1.5
B = 0.75


class LexicalIndex:
    def __init__(self, entries: Sequence[IndexEntry]) -> None:
        self._entries = tuple(entries)
        corpus = [tokenize(entry.text) for entry in self._entries]
        self._lengths = [len(tokens) for tokens in corpus]
        self._avg_length = sum(self._lengths) / len(corpus)
        self._frequencies = [Counter(tokens) for tokens in corpus]

        documents_containing: Counter[str] = Counter()
        for tokens in corpus:
            documents_containing.update(set(tokens))
        total = len(corpus)
        self._idf = {
            term: math.log(1 + (total - count + 0.5) / (count + 0.5))
            for term, count in documents_containing.items()
        }

    @classmethod
    def build(cls, documents: Sequence[RunbookDocument]) -> LexicalIndex:
        return cls(build_entries(documents))

    @property
    def entries(self) -> tuple[IndexEntry, ...]:
        return self._entries

    def _score(self, position: int, terms: Sequence[str]) -> float:
        frequencies = self._frequencies[position]
        normalised = 1 - B + B * self._lengths[position] / self._avg_length
        total = 0.0
        for term in terms:
            frequency = frequencies.get(term, 0)
            if not frequency:
                continue
            total += self._idf.get(term, 0.0) * frequency * (K1 + 1) / (frequency + K1 * normalised)
        return total

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        """Matching documents, best first, ties broken by `document_id`.

        Zero-score documents are omitted rather than padding the result to `k`.
        Exactly 1 of the 71 labelled queries scores 0.0 against its own runbook
        and it already sits at rank 13, so this costs 0.000 Recall@5 -- and it
        keeps 7 unmatched documents out of RRF, where an alphabetical ordering
        would otherwise look like evidence.
        """
        terms = tokenize(query)
        if not terms:
            return []

        scored = [
            (entry.document_id, score)
            for position, entry in enumerate(self._entries)
            if (score := self._score(position, terms)) > 0.0
        ]
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return scored[:k]
