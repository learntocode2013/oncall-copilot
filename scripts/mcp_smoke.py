"""Prove a running mcp-runbooks container actually serves its contract.

Run INSIDE the container (`docker exec`), where the `mcp` client library is
already installed at the pinned version:

    docker exec <container> python /tmp/mcp_smoke.py

Exits non-zero with a reason on the first failed assertion. This is the check
`make verify` cannot do: verify's probe proves the port is bound, and a bound
port is exactly what a server that has silently fallen back to the lexical
baseline also has.
"""

from __future__ import annotations

import json
import sys

import anyio
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

URL = "http://localhost:8102/mcp"
PROBE = "checkout returning 422"


def unwrap(result) -> object:
    """MCP returns structured content for both tools, but a list-returning tool
    wraps it under "result" while an object-returning one does not."""
    structured = result.structured_content
    if structured:
        return structured.get("result", structured)
    return json.loads(result.content[0].text)


async def main() -> None:
    async with (
        streamable_http_client(URL) as (read, write),
        ClientSession(read, write) as session,
    ):
        init = await session.initialize()
        instructions = init.instructions or ""
        print(f"server: {init.server_info.name} | {instructions}")

        # The point of the whole check. A server serving the baseline while
        # everyone believes it serves config B is the failure the MCP
        # instructions, `configure`'s refusal to fall back, and this
        # assertion all exist to prevent.
        assert "hybrid" in instructions, f"not serving hybrid retrieval: {instructions!r}"
        assert "LEXICAL BASELINE" not in instructions, "silently degraded to the baseline"

        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        assert set(tools) == {"search_runbooks", "get_runbook"}, f"tools: {sorted(tools)}"
        for name, tool in tools.items():
            assert tool.annotations is not None, f"{name} has no annotations"
            assert tool.annotations.read_only_hint is True, f"{name} not read-only"

        hits = unwrap(await session.call_tool("search_runbooks", {"query": PROBE, "k": 3}))
        assert hits, f"no hits for {PROBE!r}"
        print(f"search_runbooks -> {len(hits)} hits")
        for hit in hits:
            print(
                f"  {hit['document_id']:28s} status={hit['status']:11s} "
                f"lex={hit['lexical_rank']} sem={hit['semantic_rank']}"
            )

        # Both halves must have run. If every hit is missing one rank the
        # retriever is half dead, which no count of results would reveal.
        assert any(h["semantic_rank"] is not None for h in hits), "no semantic ranks: BM25 only"
        assert any(h["lexical_rank"] is not None for h in hits), "no lexical ranks"

        runbook = unwrap(
            await session.call_tool("get_runbook", {"document_id": hits[0]["document_id"]})
        )
        assert runbook["body"], "get_runbook returned an empty body"
        assert "queries" not in runbook, "get_runbook leaked the labelled queries"
        print(
            f"get_runbook {runbook['document_id']}: {len(runbook['body'].split())} words, no leak"
        )


def assertions(exc: BaseException) -> list[str]:
    """anyio raises inside a task group, so an AssertionError arrives wrapped in
    one or more ExceptionGroups. Unwrapped, the failure is one line; wrapped, it
    is forty lines of event-loop frames with the reason at the bottom."""
    if isinstance(exc, BaseExceptionGroup):
        return [message for sub in exc.exceptions for message in assertions(sub)]
    if isinstance(exc, AssertionError):
        return [str(exc)]
    return []


if __name__ == "__main__":
    try:
        anyio.run(main)
    # Broad on purpose: a smoke check reports a failure, it does not handle one.
    except BaseException as exc:
        failures = assertions(exc)
        if not failures:
            raise
        for message in failures:
            print(f"FAIL: {message}", file=sys.stderr)
        raise SystemExit(1) from exc
    print("mcp-runbooks: contract OK")
