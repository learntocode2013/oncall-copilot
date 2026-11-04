"""Run one retrieval configuration and write down what happened.

A record exists so a number can be defended later. Three things make that
possible, and none of them is the metric: which ruler (`query_set_hash`), which
embeddings or none (`model_id` and `model_revision`), and which queries moved
(every per-query rank).

The record is flat on purpose -- Task 7's `compare` reads it directly, and a
human opening one of these files should not have to navigate nesting to find
the number.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from oncall_knowledge.evaluation import EvalResult, dev_cases, evaluate, load_cases
from oncall_knowledge.indexing.hybrid import DEFAULT_K0
from oncall_knowledge.ingestion import ingest_runbooks
from oncall_knowledge.retrieval import Retriever

DEFAULT_OUT_DIR = Path("artifacts/retrieval")
CONFIG_IDS = ("A", "B")
METRICS = ("recall_at_1", "recall_at_5", "mrr")

#: A rank of None is a miss, and a miss is worse than any rank.
WORST_RANK = float("inf")


def build_record(
    config_id: str,
    result: EvalResult,
    *,
    query_set_name: str,
    n_documents: int,
    k0: int = DEFAULT_K0,
    model_id: str | None = None,
    model_revision: str | None = None,
) -> dict:
    """One run as JSON-ready data.

    `model_id` is present and None for config A rather than omitted: a missing
    key reads as "we forgot to record it", null as "there is no model", and
    `compare` has to tell those apart.

    `k0` is recorded for every config, including A where it is inert. Config B's
    number is meaningless without it -- the same two indexes score Recall@5
    0.972 at k0=60 and 1.000 at k0=1 (D9) -- and one uniform shape means
    `compare` never branches on config id.

    `gold` is sorted, not just listed. A frozenset is not JSON, and an unsorted
    list would make two identical runs produce different files.
    """
    return {
        "config_id": config_id,
        "model_id": model_id,
        "model_revision": model_revision,
        "k0": k0,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "query_set_name": query_set_name,
        "query_set_hash": result.query_set_hash,
        "n_cases": result.n_cases,
        "n_documents": n_documents,
        "recall_at_1": result.recall_at_1,
        "recall_at_5": result.recall_at_5,
        "mrr": result.mrr,
        "cases": [
            {
                "query": case.query,
                "gold": sorted(case.gold),
                "rank": case.rank,
                "top": list(case.top),
            }
            for case in result.cases
        ],
    }


def write_record(record: dict, out_dir: Path = DEFAULT_OUT_DIR) -> Path:
    """Write the record under a name no earlier run can own.

    Config A will be run many times and the history is the evidence, so a fixed
    filename would keep only the last one. The second-resolution timestamp can
    collide when two runs land in the same second, hence the counter.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = (
        f"{record['config_id']}-{record['query_set_hash'][:8]}-"
        f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
    )
    path = out_dir / f"{stem}.json"
    attempt = 2
    while path.exists():
        path = out_dir / f"{stem}-{attempt}.json"
        attempt += 1

    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


def _require_agreement(before: dict, after: dict, field: str, why: str) -> None:
    """Refuse a comparison across a changed condition, naming both values.

    An error that says only "mismatch" sends the reader off to diff two JSON
    files; naming the values makes the cause readable where it is raised.
    """
    if before[field] != after[field]:
        raise ValueError(
            f"cannot compare runs with different {field}: "
            f"{before[field]!r} then {after[field]!r}. {why}"
        )


def _rank_order(rank: int | None) -> float:
    return WORST_RANK if rank is None else float(rank)


def compare(before: dict, after: dict) -> dict:
    """The before/after report, or a refusal.

    Three conditions must hold, and each one, if violated, produces a delta that
    looks exactly like a finding while raising nothing:

    the same ruler -- someone rewording a query or narrowing a gold label
    between runs makes the delta measure the edit;

    the same corpus -- Recall@5 over 26 documents is a harder task than over 18,
    so the 8 pending seed postmortems must land strictly before the first run or
    strictly after the last (design §10). This check is what makes that
    enforceable rather than advisory;

    the same queries -- a matching hash with differing queries means a record
    was hand-edited.
    """
    _require_agreement(
        before,
        after,
        "query_set_hash",
        "the query set was edited between the two runs, so the delta measures the edit.",
    )
    _require_agreement(
        before,
        after,
        "n_documents",
        "the corpus changed size, which moves every metric on its own.",
    )

    before_cases = {case["query"]: case for case in before["cases"]}
    after_cases = {case["query"]: case for case in after["cases"]}
    if before_cases.keys() != after_cases.keys():
        only_before = sorted(before_cases.keys() - after_cases.keys())
        only_after = sorted(after_cases.keys() - before_cases.keys())
        raise ValueError(
            "the two runs scored different queries despite a matching hash, so a "
            f"record was hand-edited. only in before: {only_before}; "
            f"only in after: {only_after}"
        )

    improved, regressed = [], []
    for query, case in before_cases.items():
        was, now = _rank_order(case["rank"]), _rank_order(after_cases[query]["rank"])
        if now < was:
            improved.append(query)
        elif now > was:
            regressed.append(query)

    return {
        "before": before["config_id"],
        "after": after["config_id"],
        "query_set_name": before["query_set_name"],
        "query_set_hash": before["query_set_hash"],
        "n_cases": before["n_cases"],
        "n_documents": before["n_documents"],
        "metrics": {
            "before": {name: before[name] for name in METRICS},
            "after": {name: after[name] for name in METRICS},
        },
        "deltas": {name: after[name] - before[name] for name in METRICS},
        "improved": improved,
        "regressed": regressed,
    }


def _print_comparison(report: dict) -> None:
    print(
        f"{report['before']} -> {report['after']}  on {report['query_set_name']} "
        f"({report['n_cases']} cases, {report['n_documents']} documents)"
    )
    for name in METRICS:
        before = report["metrics"]["before"][name]
        after = report["metrics"]["after"][name]
        print(f"  {name:<12} {before:.3f} -> {after:.3f}   {report['deltas'][name]:+.3f}")
    print(f"  improved  {len(report['improved'])}: {report['improved']}")
    print(f"  regressed {len(report['regressed'])}: {report['regressed']}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="oncall_knowledge.experiment")
    parser.add_argument("--config", choices=CONFIG_IDS)
    parser.add_argument("--cases", default="dev", help="'dev' or a path to P4's query YAML")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--k0",
        type=int,
        default=DEFAULT_K0,
        help="RRF k0. Config B ships the default; tune on dev, report on P4's set (D9).",
    )
    parser.add_argument(
        "--compare",
        nargs=2,
        type=Path,
        metavar=("BEFORE", "AFTER"),
        help="two run records to compare instead of running a config",
    )
    args = parser.parse_args(argv)

    if args.compare:
        before, after = (json.loads(path.read_text(encoding="utf-8")) for path in args.compare)
        _print_comparison(compare(before, after))
        return 0

    if not args.config:
        parser.error("one of --config or --compare is required")

    documents = ingest_runbooks()
    if args.cases == "dev":
        cases, query_set_name = dev_cases(documents), "dev"
    else:
        path = Path(args.cases)
        known = {document.document_id for document in documents}
        cases, query_set_name = load_cases(path, known_ids=known), path.stem

    # Both configs go through Retriever so an A/B comparison differs in the
    # retriever and in nothing else -- not in the measurement path as well.
    semantic = args.config == "B"
    retriever = Retriever.build(documents, semantic=semantic, k0=args.k0)

    model_id = model_revision = None
    if semantic:
        from oncall_knowledge.indexing.semantic import SemanticIndex

        model_id, model_revision = SemanticIndex.MODEL_ID, SemanticIndex.MODEL_REVISION

    result = evaluate(retriever.search, cases)
    record = build_record(
        args.config,
        result,
        query_set_name=query_set_name,
        n_documents=len(documents),
        k0=args.k0,
        model_id=model_id,
        model_revision=model_revision,
    )
    path = write_record(record, out_dir=args.out)

    print(f"config {args.config} on {query_set_name} ({result.n_cases} cases, k0={args.k0})")
    print(f"  recall_at_1 {result.recall_at_1:.3f}")
    print(f"  recall_at_5 {result.recall_at_5:.3f}")
    print(f"  mrr         {result.mrr:.3f}")
    print(f"  query_set_hash {record['query_set_hash'][:12]}")
    print(f"written to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
