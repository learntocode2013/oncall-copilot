"""Retrieval metrics, and the ruler they are computed against.

Gold is a set (D12), so a hit is any member appearing in the top k. Recall@5 is
the graded metric and is fixed at 5 -- a field named recall_at_5 that silently
reported recall@k would mislead whoever reads a run record later. `depth` stays
configurable because MRR must not be censored at 5: with Recall@5 saturated at
0.972 on 18 documents, MRR is what still discriminates.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from oncall_knowledge.schema import RunbookDocument

RECALL_K = 5

#: (query, depth) -> [(document_id, score)]. LexicalIndex.search and the hybrid
#: retriever both satisfy this, which is what lets one evaluate() score both.
SearchFn = Callable[[str, int], list[tuple[str, float]]]


@dataclass(frozen=True, slots=True)
class QueryCase:
    """One labelled query. `gold` is a frozenset so the case stays hashable."""

    query: str
    gold: frozenset[str]


@dataclass(frozen=True, slots=True)
class CaseResult:
    query: str
    gold: frozenset[str]
    rank: int | None
    top: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvalResult:
    recall_at_1: float
    recall_at_5: float
    mrr: float
    cases: tuple[CaseResult, ...]
    query_set_hash: str

    @property
    def n_cases(self) -> int:
        return len(self.cases)


def rank_of_first_gold(ranked_ids: Sequence[str], gold: frozenset[str]) -> int | None:
    """1-based rank of the best-placed gold member, or None for a miss.

    None rather than 0: a miss must not be falsy-indistinguishable from rank 1,
    and must never reach arithmetic as 1/0. Scanning in rank order means the
    BEST-placed member wins, not an arbitrary one from an unordered set.
    """
    for position, document_id in enumerate(ranked_ids, start=1):
        if document_id in gold:
            return position
    return None


def dev_cases(documents: Sequence[RunbookDocument]) -> list[QueryCase]:
    """The 71 front-matter queries. A DEVELOPMENT set: each was written by the
    author of the runbook it retrieves, in the same sitting, with a median
    query/document vocabulary overlap of 0.75. Graded numbers need P4's set.
    """
    return [
        QueryCase(query=query, gold=frozenset({document.document_id}))
        for document in documents
        for query in document.metadata.queries
    ]


def load_cases(path: Path, *, known_ids: Collection[str]) -> list[QueryCase]:
    """P4's query set: a `queries` list of {query, gold}, gold one id or a list.

    `known_ids` is required, not optional. A typo'd gold label would otherwise
    produce an unscoreable case that looks exactly like a retrieval miss and
    lowers the score for the wrong reason -- and P1 cannot catch it by reading,
    because P1 did not author the labels.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = raw.get("queries")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{path}: expected a non-empty 'queries' list")

    known = set(known_ids)
    cases: list[QueryCase] = []
    for position, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            # ValueError, not TypeError: every malformed-input failure in this
            # loader is a ValueError so one `except ValueError` in the experiment
            # runner reports "P4's query file is wrong" instead of letting a
            # TypeError escape as a crash. Same convention as schema.py.
            raise ValueError(f"{path}: queries[{position}] must be a mapping")  # noqa: TRY004

        query = entry.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError(f"{path}: queries[{position}] needs a non-empty 'query'")

        declared = entry.get("gold")
        if declared is None:
            raise ValueError(f"{path}: {query!r} has no 'gold' label")
        gold = [declared] if isinstance(declared, str) else list(declared)
        if not gold or not all(isinstance(g, str) and g.strip() for g in gold):
            raise ValueError(f"{path}: {query!r} has an empty or non-string 'gold' label")

        unknown = sorted(set(gold) - known)
        if unknown:
            raise ValueError(
                f"{path}: {query!r} names gold label(s) that are not in the corpus: "
                f"{', '.join(unknown)}"
            )
        cases.append(QueryCase(query=query.strip(), gold=frozenset(gold)))
    return cases


def query_set_hash(cases: Sequence[QueryCase]) -> str:
    """Fingerprint of the ruler. Sorted, so a reordered file is the same set.

    Every run record carries this. Comparing two runs whose hashes differ is a
    comparison against two different rulers, which produces a delta that looks
    exactly like a result.
    """
    digest = hashlib.sha256()
    for query, gold in sorted((c.query, tuple(sorted(c.gold))) for c in cases):
        digest.update(query.encode("utf-8"))
        digest.update(b"\x00")
        digest.update("\x00".join(gold).encode("utf-8"))
        digest.update(b"\x1e")
    return digest.hexdigest()


def evaluate(search: SearchFn, cases: Sequence[QueryCase], *, depth: int = 20) -> EvalResult:
    """Score one search callable over one case set, keeping every per-query rank.

    Raises on an empty case set rather than reporting 0.0 across the board: an
    empty set means a path or glob is wrong, and zeros would read as a terrible
    retriever instead of a missing file.
    """
    if not cases:
        raise ValueError("no cases to evaluate")

    results: list[CaseResult] = []
    hits_at_1 = hits_at_5 = 0
    reciprocal = 0.0
    for case in cases:
        ranked = [document_id for document_id, _ in search(case.query, depth)]
        rank = rank_of_first_gold(ranked, case.gold)
        if rank is not None:
            hits_at_1 += rank == 1
            hits_at_5 += rank <= RECALL_K
            reciprocal += 1 / rank
        results.append(CaseResult(case.query, case.gold, rank, tuple(ranked[:RECALL_K])))

    total = len(cases)
    return EvalResult(
        recall_at_1=hits_at_1 / total,
        recall_at_5=hits_at_5 / total,
        mrr=reciprocal / total,
        cases=tuple(results),
        query_set_hash=query_set_hash(cases),
    )
