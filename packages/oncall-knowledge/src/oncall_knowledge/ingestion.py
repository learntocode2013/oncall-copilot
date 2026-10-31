from pathlib import Path

from .schema import RunbookDocument, ValidationIssue
from .validator import DEFAULT_FLAGD_FILE, DEFAULT_RUNBOOK_DIR, validate_corpus


class IngestionError(Exception):
    """Raised when the runbook corpus ingestion fails"""

    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        details = "\n".join([str(issue) for issue in self.issues])
        super().__init__(f"Runbook ingestion failed due to:\n {details}")


def ingest_runbooks(
    runbook_dir: Path = DEFAULT_RUNBOOK_DIR,
    flagd_path: Path = DEFAULT_FLAGD_FILE,
) -> list[RunbookDocument]:
    runbook_corpus_root = DEFAULT_RUNBOOK_DIR.resolve()
    requested_runbook_root = runbook_dir.resolve()

    if runbook_corpus_root != requested_runbook_root:
        raise ValueError(
            f"Ingestion target for runbooks can only be {runbook_corpus_root}. "
            f"{requested_runbook_root} is restricted. "
        )

    documents, issues = validate_corpus(
        runbook_dir=runbook_dir, flagd_path=flagd_path, manifest_path=None
    )
    if issues:
        raise IngestionError(issues)

    return documents
