"""Task 7: the before/after comparison, and the two things it must refuse.

A delta is only a result if both runs were scored against the same ruler on the
same corpus. Two failure modes produce a number that looks exactly like a
finding and raises nothing:

A silently edited query set. Someone rewords a query or narrows a gold label
between run A and run B, and the delta measures the edit. `query_set_hash`
catches it.

A changed corpus. The 8 unwritten seed postmortems take the corpus from 18
documents to 26, which moves every metric on its own -- Recall@5 over 26
documents is a harder task than over 18. `n_documents` catches it, which is why
the design says the postmortems land strictly before run A or strictly after
run B (section 10).
"""

import json
from pathlib import Path

import pytest
from oncall_knowledge.experiment import compare

HASH = "a" * 64


def record(config_id: str, *, hash_: str = HASH, n_documents: int = 18, cases=(), **metrics):
    base = {"recall_at_1": 0.6, "recall_at_5": 0.9, "mrr": 0.7}
    base.update(metrics)
    return {
        "config_id": config_id,
        "model_id": None,
        "model_revision": None,
        "created_at": "2026-11-04T00:00:00+00:00",
        "query_set_name": "dev",
        "query_set_hash": hash_,
        "n_cases": len(cases),
        "n_documents": n_documents,
        "cases": list(cases),
        **base,
    }


def case(query: str, rank, gold=("g",)):
    return {"query": query, "gold": list(gold), "rank": rank, "top": []}


# --- the refusals --------------------------------------------------------


def test_comparing_different_query_sets_refuses():
    """A silently edited ruler produces a quietly meaningless delta."""
    with pytest.raises(ValueError, match="query_set_hash"):
        compare(record("A"), record("B", hash_="b" * 64))


def test_comparing_different_corpus_sizes_refuses():
    """18 documents versus 26 is a different task, not a different retriever."""
    with pytest.raises(ValueError, match="n_documents"):
        compare(record("A"), record("B", n_documents=26))


def test_the_refusal_names_both_values():
    """An error that says "mismatch" sends you to read two JSON files."""
    with pytest.raises(ValueError) as caught:
        compare(record("A", n_documents=18), record("B", n_documents=26))

    assert "18" in str(caught.value)
    assert "26" in str(caught.value)


# --- the deltas ----------------------------------------------------------


def test_deltas_are_after_minus_before():
    report = compare(
        record("A", recall_at_5=0.9, recall_at_1=0.6, mrr=0.7),
        record("B", recall_at_5=1.0, recall_at_1=0.75, mrr=0.84),
    )

    assert report["deltas"]["recall_at_5"] == pytest.approx(0.1)
    assert report["deltas"]["recall_at_1"] == pytest.approx(0.15)
    assert report["deltas"]["mrr"] == pytest.approx(0.14)


def test_a_flat_metric_reports_zero_not_absence():
    """Recall@5 is EXPECTED to move by ~0 at k0=60 (D9). That is the result, so
    it has to be reported as 0.0 rather than omitted."""
    report = compare(record("A", recall_at_5=0.972), record("B", recall_at_5=0.972))

    assert report["deltas"]["recall_at_5"] == 0.0


def test_the_report_records_which_configs_were_compared():
    report = compare(record("A"), record("B"))

    assert report["before"] == "A"
    assert report["after"] == "B"


# --- which queries moved -------------------------------------------------


def test_compare_names_the_queries_that_moved():
    before = record("A", cases=[case("improved", 7), case("regressed", 1), case("static", 2)])
    after = record("B", cases=[case("improved", 2), case("regressed", 6), case("static", 2)])

    report = compare(before, after)

    assert report["improved"] == ["improved"]
    assert report["regressed"] == ["regressed"]


def test_a_miss_becoming_a_hit_counts_as_improved():
    """rank None -> 1 is the largest possible improvement and must not be
    silently dropped for being unsubtractable."""
    before = record("A", cases=[case("found at last", None)])
    after = record("B", cases=[case("found at last", 1)])

    assert compare(before, after)["improved"] == ["found at last"]


def test_a_hit_becoming_a_miss_counts_as_regressed():
    before = record("A", cases=[case("lost", 3)])
    after = record("B", cases=[case("lost", None)])

    assert compare(before, after)["regressed"] == ["lost"]


def test_two_misses_are_not_a_change():
    before = record("A", cases=[case("never found", None)])
    after = record("B", cases=[case("never found", None)])

    report = compare(before, after)

    assert report["improved"] == []
    assert report["regressed"] == []


def test_comparing_case_sets_with_different_queries_refuses():
    """Same hash but different queries means one of the records was hand-edited."""
    before = record("A", cases=[case("one", 1)])
    after = record("B", cases=[case("two", 1)])

    with pytest.raises(ValueError, match="queries"):
        compare(before, after)


# --- end to end ----------------------------------------------------------


def test_config_b_runs_and_records_the_pinned_model(tmp_path: Path):
    """An unrecorded model is an unreproducible run."""
    pytest.importorskip("sentence_transformers")
    from oncall_knowledge.experiment import main
    from oncall_knowledge.indexing.semantic import SemanticIndex

    assert main(["--config", "B", "--cases", "dev", "--out", str(tmp_path)]) == 0

    written = json.loads(next(tmp_path.glob("B-*.json")).read_text())

    assert written["model_id"] == SemanticIndex.MODEL_ID
    assert written["model_revision"] == SemanticIndex.MODEL_REVISION
    assert written["k0"] == 60


def test_a_real_a_versus_b_comparison_holds_recall_at_5_and_lifts_recall_at_1(tmp_path: Path):
    """The prediction on record in design section 6, checked against reality.

    At k0=60, Recall@5 should barely move while Recall@1 should gain clearly.
    If Recall@5 jumps on the DEV set, suspect contamination before celebrating.
    """
    pytest.importorskip("sentence_transformers")
    from oncall_knowledge.experiment import main

    assert main(["--config", "A", "--cases", "dev", "--out", str(tmp_path)]) == 0
    assert main(["--config", "B", "--cases", "dev", "--out", str(tmp_path)]) == 0

    before = json.loads(next(tmp_path.glob("A-*.json")).read_text())
    after = json.loads(next(tmp_path.glob("B-*.json")).read_text())
    report = compare(before, after)

    assert report["deltas"]["recall_at_5"] == pytest.approx(0.0, abs=0.02)
    assert report["deltas"]["recall_at_1"] > 0.05
