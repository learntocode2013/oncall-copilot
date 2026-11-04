"""The runbooks MCP server, against the contract in CONTRIBUTING.md.

Written by P1 while P3 is on leave. The server is a thin wrapper over
oncall-knowledge's Retriever: it adds the tool schema, the excerpt, and the
metadata join, and it adds no retrieval logic of its own. If a test here needs
to reason about ranking, it belongs in oncall-knowledge instead.

Every test runs the tool functions directly rather than over a transport. The
transport is the MCP SDK's concern; what this package owns is the contract.
"""

import pytest
from oncall_tools.servers.runbooks_kb import (
    EXCERPT_WORDS,
    build_server,
    configure,
    get_runbook,
    search_runbooks,
)


@pytest.fixture(autouse=True)
def lexical_only():
    """Contract tests run on the lexical baseline.

    The contract is about the tool's shape -- fields, excerpts, status
    exposure, the absence of filter arguments -- none of which depends on which
    retriever is behind it. Running them lexically means they run in CI, which
    installs no torch. The two tests that genuinely need the model importorskip.
    """
    configure(semantic=False)
    yield
    configure(semantic=True)


# --- search_runbooks -----------------------------------------------------


def test_search_returns_hits_in_rank_order():
    hits = search_runbooks("emailMemoryLeak", k=3)

    assert hits
    assert hits[0]["document_id"] == "email-memory-leak"
    assert [h["score"] for h in hits] == sorted((h["score"] for h in hits), reverse=True)


def test_search_respects_k():
    assert len(search_runbooks("payment", k=2)) <= 2


def test_an_empty_query_returns_nothing():
    assert search_runbooks("") == []
    assert search_runbooks("   \n\t ") == []


def test_every_hit_carries_the_contract_fields():
    hit = search_runbooks("checkout 422", k=1)[0]

    assert set(hit) == {
        "document_id",
        "status",
        "service",
        "severity",
        "excerpt",
        "score",
        "lexical_rank",
        "semantic_rank",
    }


def test_the_excerpt_is_bounded_and_not_the_whole_body():
    """Median body is 444 tokens; five of those is ~2,200 tokens per search.
    The excerpt is what makes two tools cheaper than one."""
    hit = search_runbooks("checkout 422", k=1)[0]

    assert 0 < len(hit["excerpt"].split()) <= EXCERPT_WORDS


def test_the_excerpt_never_contains_a_labelled_query():
    """The leak that takes Recall@1 from 0.648 to 0.958 must not reappear in the
    tool's output just because the index itself is clean."""
    from oncall_knowledge.ingestion import ingest_runbooks

    queries_by_id = {d.document_id: d.metadata.queries for d in ingest_runbooks()}

    for hit in search_runbooks("checkout is failing", k=5):
        for query in queries_by_id[hit["document_id"]]:
            assert query not in hit["excerpt"]


def test_ranks_say_which_half_found_it():
    """lexical_rank=None with semantic_rank set means only the embedding found
    this -- the difference between a single-source hit and agreement."""
    hit = search_runbooks("emailMemoryLeak", k=1)[0]

    assert hit["lexical_rank"] == 1
    assert "semantic_rank" in hit


# --- D13: status is exposed, never acted on ------------------------------


def test_status_is_returned_not_filtered():
    """14 of 18 runbooks are `structural` and 2 `hypothesis`. The agent must be
    able to say "the only runbook I found is unverified"."""
    from oncall_knowledge.ingestion import ingest_runbooks

    documents = ingest_runbooks()
    for document in (d for d in documents if d.metadata.status == "hypothesis"):
        reachable = {
            hit["document_id"]
            for query in document.metadata.queries
            for hit in search_runbooks(query, k=5)
        }

        assert document.document_id in reachable, f"{document.document_id} unreachable"


def test_search_takes_no_filter_arguments():
    """A service or status filter would let the agent narrow to what it already
    believes, which is how a wrong hypothesis gets confirmed."""
    import inspect

    parameters = set(inspect.signature(search_runbooks).parameters)

    assert parameters == {"query", "k"}


# --- get_runbook ---------------------------------------------------------


def test_get_runbook_returns_the_body_and_the_fix():
    runbook = get_runbook("payment-failure")

    assert runbook["document_id"] == "payment-failure"
    assert len(runbook["body"].split()) > EXCERPT_WORDS
    assert runbook["fix"]["action"] == "set_flag"
    assert runbook["preconditions"]


def test_get_runbook_on_an_unknown_id_raises_rather_than_returning_empty():
    """An empty dict would read to the model as "this runbook says nothing"."""
    with pytest.raises(KeyError, match="no-such-runbook"):
        get_runbook("no-such-runbook")


def test_get_runbook_never_returns_the_labelled_queries():
    """They are evaluation labels, not operational content, and putting them in
    front of the model is the same leak one layer further out."""
    from oncall_knowledge.ingestion import ingest_runbooks

    payment = next(d for d in ingest_runbooks() if d.document_id == "payment-failure")
    runbook = get_runbook("payment-failure")

    assert "queries" not in runbook
    for query in payment.metadata.queries:
        assert query not in runbook["body"]


# --- the server itself ---------------------------------------------------


def test_the_server_registers_exactly_the_two_contract_tools():
    import anyio

    server = build_server(semantic=False)
    tools = anyio.run(server.list_tools)

    assert {tool.name for tool in tools} == {"search_runbooks", "get_runbook"}


def test_both_tools_are_annotated_read_only():
    """P3's write isolation is enforced in permissions.py, but a read tool that
    does not declare itself read-only cannot be checked by anything."""
    import anyio

    server = build_server(semantic=False)
    tools = anyio.run(server.list_tools)

    for tool in tools:
        assert tool.annotations is not None, tool.name
        assert tool.annotations.read_only_hint is True, tool.name


def test_the_score_is_documented_as_incomparable_across_queries():
    """It is an RRF sum over 1/(k0+rank): no absolute meaning, no stable scale.
    If the description does not say so, the model will threshold it."""
    import anyio

    server = build_server(semantic=False)
    search = next(t for t in anyio.run(server.list_tools) if t.name == "search_runbooks")

    assert "compare" in search.description.lower()


# --- the mode is chosen, never inferred ----------------------------------


def test_the_server_declares_which_retriever_it_serves():
    """A server quietly serving the baseline while everyone believes it serves
    the hybrid is the failure config B's tests exist to prevent."""
    baseline = build_server(semantic=False)

    assert "LEXICAL BASELINE" in baseline.instructions


def test_asking_for_semantic_without_the_extra_raises():
    """No automatic fallback: serving the baseline is a decision, not a default
    that happens when a dependency is missing."""
    import sys

    from oncall_knowledge.indexing.semantic import SemanticUnavailableError

    if sys.modules.get("sentence_transformers"):
        pytest.skip("the extra is installed; this asserts the behaviour without it")

    configure(semantic=True)
    with pytest.raises(SemanticUnavailableError, match="uv sync --extra semantic"):
        search_runbooks("payment")
