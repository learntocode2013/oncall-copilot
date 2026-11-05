from pathlib import Path

import pytest
from oncall_knowledge.ingestion import (
    ingest_runbooks,
)


def test_ingestion_loads_authored_runbooks():
    documents = ingest_runbooks()

    assert documents
    assert all(document.body for document in documents)
    assert all(document.document_id for document in documents)


def test_ingestion_preserves_metadata():
    documents = ingest_runbooks()

    for document in documents:
        assert document.metadata.kind in {"symptom", "cause"}
        assert document.metadata.status in {
            "verified",
            "structural",
            "hypothesis",
        }


def test_ingestion_does_not_include_frontmatter_queries():
    documents = ingest_runbooks()

    for document in documents:
        for query in document.metadata.queries:
            assert query not in document.body


def test_ingestion_rejects_non_runbook_directory(tmp_path: Path):
    with pytest.raises(ValueError, match="restricted"):
        ingest_runbooks(runbook_dir=tmp_path)


def test_ingestion_globs_exclude_the_eval_tree():
    documents = ingest_runbooks()

    for document in documents:
        resolved = Path(document.source_path).resolve()

        assert "eval" not in resolved.parts
        assert "runbooks" in resolved.parts
