"""Task 4: running a configuration and writing down what happened.

A record exists so a number can be defended later. Three things make that
possible and none of them are the metric: the query-set hash (which ruler),
the model id (which embeddings, or none), and every per-query rank (which
queries moved). A run that reports 0.972 and nothing else cannot support or
refute any claim about why.

Config A is lexical only and needs no model. Config B adds the semantic half,
and its record MUST carry the model id -- an unrecorded model is an
unreproducible run, and the pinned revision is only a pin if it is written down
next to the number it produced.
"""

import json
from pathlib import Path

import pytest
from oncall_knowledge.evaluation import dev_cases, evaluate
from oncall_knowledge.experiment import build_record, main, write_record
from oncall_knowledge.indexing.lexical import LexicalIndex
from oncall_knowledge.ingestion import ingest_runbooks


@pytest.fixture(scope="module")
def documents():
    return ingest_runbooks()


@pytest.fixture(scope="module")
def lexical_result(documents):
    return evaluate(LexicalIndex.build(documents).search, dev_cases(documents))


# --- the baseline itself -------------------------------------------------


def test_baseline_reproduces_the_measured_recall(lexical_result):
    """Measured 0.972 / 0.648 / 0.787 with this corpus, tokenizer and scorer.

    Floors rather than exact equality: the 8 unwritten seed postmortems will
    change the corpus, and that is a conscious decision made elsewhere, not a
    regression. If either floor breaks, retrieval regressed or the corpus moved
    -- both worth stopping for.
    """
    assert lexical_result.recall_at_5 >= 0.95
    assert lexical_result.recall_at_1 >= 0.60
    assert lexical_result.mrr >= 0.75


def test_the_baseline_is_deterministic(documents):
    """Two runs must agree exactly, or an A/B delta is partly noise."""
    cases = dev_cases(documents)
    first = evaluate(LexicalIndex.build(documents).search, cases)
    second = evaluate(LexicalIndex.build(documents).search, cases)

    assert (first.recall_at_1, first.recall_at_5, first.mrr) == (
        second.recall_at_1,
        second.recall_at_5,
        second.mrr,
    )
    assert [c.rank for c in first.cases] == [c.rank for c in second.cases]


# --- the record ----------------------------------------------------------


def test_record_carries_what_a_number_needs_to_be_defended(lexical_result):
    record = build_record("A", lexical_result, query_set_name="dev", n_documents=18)

    assert record["config_id"] == "A"
    assert record["query_set_hash"] == lexical_result.query_set_hash
    assert record["n_cases"] == lexical_result.n_cases
    assert record["n_documents"] == 18
    assert record["recall_at_5"] == lexical_result.recall_at_5
    assert len(record["cases"]) == lexical_result.n_cases


def test_config_a_records_no_model(lexical_result):
    """None, present: a missing key reads as "we forgot", null as "there isn't
    one". Task 7 has to tell those apart."""
    record = build_record("A", lexical_result, query_set_name="dev", n_documents=18)

    assert "model_id" in record
    assert record["model_id"] is None


def test_every_case_keeps_its_rank_and_top_ranking(lexical_result):
    record = build_record("A", lexical_result, query_set_name="dev", n_documents=18)

    case = record["cases"][0]

    assert set(case) >= {"query", "gold", "rank", "top"}
    assert isinstance(case["gold"], list)  # frozenset would not serialise
    assert case["gold"] == sorted(case["gold"])  # stable across runs


def test_the_record_is_json_serialisable(lexical_result):
    record = build_record("A", lexical_result, query_set_name="dev", n_documents=18)

    reloaded = json.loads(json.dumps(record))

    assert reloaded["query_set_hash"] == record["query_set_hash"]


def test_a_miss_is_recorded_as_null_not_zero(lexical_result):
    """rank 0 would silently become a hit in any `rank <= 5` arithmetic."""
    record = build_record("A", lexical_result, query_set_name="dev", n_documents=18)

    assert all(c["rank"] is None or c["rank"] >= 1 for c in record["cases"])


# --- writing it down -----------------------------------------------------


def test_write_record_names_the_file_after_the_config(lexical_result, tmp_path: Path):
    record = build_record("A", lexical_result, query_set_name="dev", n_documents=18)

    path = write_record(record, out_dir=tmp_path)

    assert path.parent == tmp_path
    assert path.name.startswith("A-")
    assert path.suffix == ".json"


def test_two_runs_do_not_overwrite_each_other(lexical_result, tmp_path: Path):
    """Config A will be run many times. A fixed filename would keep only the
    last one, and the history is the evidence."""
    record = build_record("A", lexical_result, query_set_name="dev", n_documents=18)

    first = write_record(record, out_dir=tmp_path)
    second = write_record(record, out_dir=tmp_path)

    assert first != second
    assert len(list(tmp_path.glob("A-*.json"))) == 2


def test_write_record_creates_the_directory(lexical_result, tmp_path: Path):
    record = build_record("A", lexical_result, query_set_name="dev", n_documents=18)

    path = write_record(record, out_dir=tmp_path / "artifacts" / "retrieval")

    assert path.is_file()


# --- the CLI -------------------------------------------------------------


def test_main_runs_config_a_and_writes_one_record(tmp_path: Path, capsys):
    exit_code = main(["--config", "A", "--cases", "dev", "--out", str(tmp_path)])
    printed = capsys.readouterr().out

    assert exit_code == 0
    assert len(list(tmp_path.glob("A-*.json"))) == 1
    assert "recall_at_5" in printed


def test_main_rejects_an_unknown_config(tmp_path: Path):
    with pytest.raises(SystemExit):
        main(["--config", "Z", "--cases", "dev", "--out", str(tmp_path)])


def test_config_b_records_the_pinned_model(tmp_path: Path):
    """An unrecorded model is an unreproducible run."""
    pytest.importorskip("sentence_transformers")
    from oncall_knowledge.indexing.semantic import SemanticIndex

    assert main(["--config", "B", "--cases", "dev", "--out", str(tmp_path)]) == 0

    record = json.loads(next(tmp_path.glob("B-*.json")).read_text())

    assert record["model_id"] == SemanticIndex.MODEL_ID
    assert record["model_revision"] == SemanticIndex.MODEL_REVISION
