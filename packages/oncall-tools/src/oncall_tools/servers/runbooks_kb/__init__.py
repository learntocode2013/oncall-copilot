"""Search runbooks and past postmortems. Read-only.

A thin wrapper over `oncall_knowledge.retrieval.Retriever`, implementing the
`mcp-runbooks` contract in CONTRIBUTING.md. It adds the tool schema, the
excerpt, and the metadata join; it adds no retrieval logic. Anything that has
to reason about ranking belongs in oncall-knowledge.

Written by P1 while P3 is on leave. P3 owns this package.

Two tools rather than one: the median runbook body is 444 tokens, so returning
five whole bodies costs ~2,200 tokens per search. `search_runbooks` returns
excerpts and `get_runbook` fetches the one the agent wants.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from oncall_knowledge.ingestion import ingest_runbooks
from oncall_knowledge.retrieval import Retriever
from oncall_knowledge.schema import RunbookDocument

SERVER_NAME = "runbooks-kb"

#: Words of body text returned with a search hit. Enough to tell two candidate
#: runbooks apart; far short of the ~440-token median body.
EXCERPT_WORDS = 60

SEARCH_DESCRIPTION = """Search the runbook corpus for incidents matching a description.

Returns the most relevant runbooks with a short excerpt of each. Call
get_runbook with a document_id to read the full body, the fix and its
preconditions.

Each hit carries `status`: `verified` means the behaviour has been observed
against the running system, `structural` means the runbook is reasoned from the
fault definition but was never injected, and `hypothesis` means it may not
apply at all. Most of this corpus is `structural`. Say so when it matters.

`lexical_rank` and `semantic_rank` show which half of the retriever found the
document. Both set means keyword and meaning search agreed; one set with the
other null means only that half found it.

`score` ranks results WITHIN one response and cannot be compared between
queries or thresholded -- it is a reciprocal-rank sum with no absolute meaning.
"""

FETCH_DESCRIPTION = """Read one runbook in full, by document_id from search_runbooks.

Returns the body, the structured fix and its preconditions. Check the
preconditions before proposing the fix: they are what separates this fault from
the ones that look like it.
"""


#: Whether this process serves the hybrid retriever or the lexical baseline.
#: Module-level because the MCP tool schema is generated from the tool
#: functions' signatures, so `search_runbooks` must not grow a mode argument
#: the model would then see and try to set.
_SEMANTIC = True


def configure(*, semantic: bool) -> None:
    """Choose the retriever before the server starts.

    There is no automatic fallback. If `sentence-transformers` is missing,
    `semantic=True` raises `SemanticUnavailableError` and the server does not
    start -- because a server that quietly served the lexical baseline while
    everyone believed it was serving the hybrid is the exact failure config B's
    tests exist to prevent. Serving the baseline is a decision someone makes,
    with `--lexical-only`, and the server says so in its instructions.
    """
    global _SEMANTIC
    _SEMANTIC = semantic
    _retriever.cache_clear()


@lru_cache(maxsize=2)
def _build_retriever(semantic: bool) -> Retriever:
    return Retriever.build(ingest_runbooks(), semantic=semantic)


def _retriever() -> Retriever:
    """Built once per mode. `Retriever.build(semantic=True)` loads an 80 MB
    model and takes ~10 seconds; per-request construction would show in every
    trace."""
    return _build_retriever(_SEMANTIC)


_retriever.cache_clear = _build_retriever.cache_clear  # type: ignore[attr-defined]


@lru_cache(maxsize=1)
def _documents() -> dict[str, RunbookDocument]:
    """`service`, `severity`, the body and the fix come from here.

    RetrievalResult carries only document_id, status, score and the two ranks,
    so the join happens in the server rather than by widening P1's public type.
    """
    return {document.document_id: document for document in ingest_runbooks()}


def _excerpt(body: str) -> str:
    return " ".join(body.split()[:EXCERPT_WORDS])


def search_runbooks(query: str, k: int = 5) -> list[dict[str, Any]]:
    """Deliberately no `service` or `status` filter.

    Both are tempting and both would let the agent narrow to what it already
    believes, which is how a wrong hypothesis gets confirmed. Filtering is a
    ranking decision and belongs to the commander's reasoning, over results it
    can see.
    """
    documents = _documents()
    return [
        {
            "document_id": result.document_id,
            "status": result.status,
            "service": documents[result.document_id].metadata.service,
            "severity": documents[result.document_id].metadata.severity,
            "excerpt": _excerpt(documents[result.document_id].body),
            "score": result.score,
            "lexical_rank": result.lexical_rank,
            "semantic_rank": result.semantic_rank,
        }
        for result in _retriever().retrieve(query, k=k)
    ]


def get_runbook(document_id: str) -> dict[str, Any]:
    """Raises KeyError on an unknown id rather than returning an empty mapping,
    which would read to the model as "this runbook says nothing"."""
    documents = _documents()
    if document_id not in documents:
        raise KeyError(f"no runbook with document_id {document_id!r}")

    document = documents[document_id]
    metadata = document.metadata
    return {
        "document_id": document_id,
        "status": metadata.status,
        "service": metadata.service,
        "severity": metadata.severity,
        "symptom": metadata.symptom,
        "body": document.body,
        "fix": (
            {"action": metadata.fix.action, "args": dict(metadata.fix.args)}
            if metadata.fix
            else None
        ),
        "preconditions": list(metadata.preconditions),
    }


def build_server(*, semantic: bool = True) -> MCPServer:
    """Register the two tools on a fresh server.

    Defaults to the hybrid retriever, which is config B and the graded
    configuration. `semantic=False` serves the lexical baseline and says so in
    the server instructions, so a client cannot mistake one for the other.

    Both tools are annotated read-only. P3's write isolation is enforced in
    permissions.py and hooks/pre_tool.py rather than by network topology, but a
    read tool that does not declare itself read-only cannot be checked by
    anything.
    """
    configure(semantic=semantic)
    served = "hybrid (BM25 + MiniLM, fused by RRF)" if semantic else "LEXICAL BASELINE ONLY"
    server = MCPServer(
        name=SERVER_NAME,
        instructions=f"Read-only search over the authored runbook corpus. Retrieval: {served}.",
    )
    read_only = ToolAnnotations(read_only_hint=True, destructive_hint=False)

    server.tool(
        name="search_runbooks",
        description=SEARCH_DESCRIPTION,
        annotations=read_only,
    )(search_runbooks)
    server.tool(
        name="get_runbook",
        description=FETCH_DESCRIPTION,
        annotations=read_only,
    )(get_runbook)
    return server
