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
from oncall_knowledge.indexing.lexical import LexicalIndex
from oncall_knowledge.ingestion import ingest_runbooks

DEFAULT_OUT_DIR = Path("artifacts/retrieval")
CONFIG_IDS = ("A", "B")


def build_record(
    config_id: str,
    result: EvalResult,
    *,
    query_set_name: str,
    n_documents: int,
    model_id: str | None = None,
    model_revision: str | None = None,
) -> dict:
    """One run as JSON-ready data.

    `model_id` is present and None for config A rather than omitted: a missing
    key reads as "we forgot to record it", null as "there is no model", and
    Task 7 has to tell those apart.

    `gold` is sorted, not just listed. A frozenset is not JSON, and an unsorted
    list would make two identical runs produce different files.
    """
    return {
        "config_id": config_id,
        "model_id": model_id,
        "model_revision": model_revision,
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="oncall_knowledge.experiment")
    parser.add_argument("--config", required=True, choices=CONFIG_IDS)
    parser.add_argument("--cases", default="dev", help="'dev' or a path to P4's query YAML")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args(argv)

    documents = ingest_runbooks()
    if args.cases == "dev":
        cases, query_set_name = dev_cases(documents), "dev"
    else:
        path = Path(args.cases)
        known = {document.document_id for document in documents}
        cases, query_set_name = load_cases(path, known_ids=known), path.stem

    if args.config == "B":
        raise NotImplementedError(
            "config B is BM25 + MiniLM + RRF and arrives with the hybrid retriever "
            "in Task 6. Running semantic-only under the label B would put two "
            "meanings behind one config id in the records."
        )

    result = evaluate(LexicalIndex.build(documents).search, cases)
    record = build_record(
        args.config,
        result,
        query_set_name=query_set_name,
        n_documents=len(documents),
    )
    path = write_record(record, out_dir=args.out)

    print(f"config {args.config} on {query_set_name} ({result.n_cases} cases)")
    print(f"  recall_at_1 {result.recall_at_1:.3f}")
    print(f"  recall_at_5 {result.recall_at_5:.3f}")
    print(f"  mrr         {result.mrr:.3f}")
    print(f"  query_set_hash {record['query_set_hash'][:12]}")
    print(f"written to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
