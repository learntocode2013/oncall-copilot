"""Tests for the corpus/manifest cross-check.

Only check_fault_coverage is covered here. The rest of validator.py is
exercised end-to-end by tests/test_guards.py::test_corpus_validates against
the real corpus.
"""

from pathlib import Path

from oncall_knowledge.schema import RunbookDocument, RunbookMetadata
from oncall_knowledge.validator import check_fault_coverage


def cause(fault_id: str) -> RunbookDocument:
    return RunbookDocument(
        document_id=fault_id,
        source_path=f"data/runbooks/{fault_id}.md",
        metadata=RunbookMetadata(
            kind="cause",
            status="structural",
            service="payment",
            symptom="something broke",
            severity="sev2",
            fault_id=fault_id,
        ),
        body="# body",
    )


def write_manifest(tmp_path: Path, *fault_ids: str) -> Path:
    entries = "\n".join(f"  - fault_id: {fid}" for fid in fault_ids)
    path = tmp_path / "manifest.yaml"
    path.write_text(f"faults:\n{entries}\n", encoding="utf-8")
    return path


def test_no_manifest_means_no_issues(tmp_path: Path):
    """Corpus validation must never depend on eval/ being built."""
    assert check_fault_coverage([cause("payment-failure")], None) == []
    assert check_fault_coverage([cause("payment-failure")], tmp_path / "absent.yaml") == []


def test_injected_fault_without_a_runbook_is_an_error(tmp_path: Path):
    manifest = write_manifest(tmp_path, "payment-failure", "cart-failure")

    issues = check_fault_coverage([cause("payment-failure")], manifest)

    errors = [i for i in issues if i.severity == "error"]
    assert len(errors) == 1
    assert "cart-failure" in errors[0].message
    assert "no runbook" in errors[0].message


def test_runbooks_ahead_of_the_manifest_collapse_to_one_warning(tmp_path: Path):
    """Sixteen identical warnings train people to ignore the validator."""
    manifest = write_manifest(tmp_path, "payment-failure")
    documents = [cause("payment-failure"), cause("cart-failure"), cause("ad-failure")]

    issues = check_fault_coverage(documents, manifest)

    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert "2 of 3" in issues[0].message
    # Still names every one of them, so the warning stays actionable.
    assert "ad-failure" in issues[0].message
    assert "cart-failure" in issues[0].message


def test_full_agreement_is_silent(tmp_path: Path):
    manifest = write_manifest(tmp_path, "payment-failure")

    assert check_fault_coverage([cause("payment-failure")], manifest) == []
