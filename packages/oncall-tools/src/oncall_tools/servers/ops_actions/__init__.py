"""Apply a runbook's fix, or roll one back. THE ONLY WRITE SERVER.

Written by P1 while P3 is on leave. P3 owns this package.

TWO TOOLS, AND THE SPLIT IS THE POINT. The corpus divides perfectly: every
`fix` in all 18 runbooks is `variant: "off"`, and every `rollback` restores the
fault variant (`100%`, `1000x`, `10sec`, `on`). The two sets of (flag, variant)
pairs are completely disjoint -- 17 of 17 rollback pairs appear in no fix.

So a single `set_flag` accepting both would be a tool that can arm every fault
in the corpus, with nothing but a variant string distinguishing "fix the
payment service" from "break it". Splitting them means a plan that wants to
re-arm a fault has to name `rollback_flag` to do it, and that name travels into
the registry, the approval payload and the audit trail.

EACH TOOL ACCEPTS ONLY PAIRS THE CORPUS NAMES. Not "any existing flag to any
existing variant": the allowlist is built from the runbooks at startup, so the
agent cannot invent a variant, cannot touch a flag no runbook knows about, and
cannot route a re-arm through the fix tool.

That allowlist is NOT the primary control -- the approval gate in
`oncall_tools.hooks.pre_tool` is, and these tools are annotated as writes so
`permissions.for_role` keeps them out of every diagnosis registry. The
allowlist is what limits the damage an approved-but-wrong call can do.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from oncall_contracts.flagd import current_variant, read_document, set_variant, write_document
from oncall_knowledge.ingestion import ingest_runbooks

SERVER_NAME = "ops-actions"

#: The same variable the lab reads, and the same file. flagd watches one
#: document; the lab edits it to inject a fault and this server edits it to fix
#: one. See oncall_contracts.flagd for why the writer is shared.
FLAG_PATH_ENV = "FLAGD_CONFIG"
DEFAULT_FLAG_PATH = Path("deploy/flagd/demo.flagd.json")

SET_FLAG_DESCRIPTION = """Apply a runbook's fix by turning a feature flag off.

Only accepts a (flag, variant) pair that some runbook names as its `fix`. Every
fix in this corpus sets a flag to `off`; to put a flag back to a fault variant,
use rollback_flag instead -- this tool will refuse it.

Requires human approval, which binds to these exact arguments. Read the
runbook's preconditions before proposing the fix: they are what separates this
fault from the ones that look like it.

Returns the variant that was in effect before the change, and whether anything
changed -- a flag already `off` is reported as `changed: false` rather than as
a fix that worked.
"""

ROLLBACK_DESCRIPTION = """Undo a fix by restoring the flag variant a runbook names as its rollback.

THIS RE-ARMS THE FAULT. Every rollback in this corpus puts a flag back to the
variant that causes the incident (`on`, `100%`, `10sec`, `1000x`). Use it only
to undo a fix that made things worse, and say so when asking for approval.

Only accepts a (flag, variant) pair that some runbook names as its `rollback`.
Requires human approval, which binds to these exact arguments.
"""


class ActionNotAllowed(ToolError):
    """The requested (flag, variant) pair is not one this tool may set.

    Raised rather than returned, so a refused action cannot read to the model as
    an action that was applied. The message names what the tool would accept for
    that flag, because "not allowed" with no alternative invites the agent to
    guess variants.

    AN `mcp` `ToolError`, NOT A `ValueError`, AND THAT IS THE WHOLE PROMISE
    ABOVE. The SDK sorts a tool's exceptions into two kinds: a `ToolError` is
    "raised deliberately", and its text travels to the caller; anything else is
    a crash, whose text deliberately STAYS ON THE SERVER. As a `ValueError`
    this class got the second treatment, so the sentence it composes with such
    care -- "this tool accepts ['off'] for 'paymentFailure'" -- was replaced by
    "Error executing tool set_flag" before any agent could read it, and every
    refusal logged a traceback as though the server had broken.
    """


def _flag_path() -> Path:
    return Path(os.environ.get(FLAG_PATH_ENV) or DEFAULT_FLAG_PATH)


@lru_cache(maxsize=1)
def _allowed() -> tuple[frozenset[tuple[str, str]], frozenset[tuple[str, str]]]:
    """(fix pairs, rollback pairs), read from the corpus once.

    Built from `ingest_runbooks()`, which pins its root to data/runbooks/ and so
    cannot reach eval/. A corpus that fails validation fails here, at startup.
    """
    fixes: set[tuple[str, str]] = set()
    rollbacks: set[tuple[str, str]] = set()
    # ingest_runbooks' OWN flagd path -- the committed baseline in the image --
    # NOT the live document this server writes. Two reasons, and the first is a
    # bug this tried to be:
    #
    # The validator rejects a document with any fault armed ("flag
    # 'paymentFailure' is committed as '100%', not 'off'; a fault left enabled
    # in the baseline breaks the shop for everyone who pulls"). That rule is
    # about the committed baseline. Validating against the LIVE document would
    # make this server refuse to start whenever a fault is active -- during
    # every incident it exists to resolve. Measured: all three apply-a-fix tests
    # failed that way.
    #
    # And it is the better boundary regardless: the allowlist comes from the
    # committed, validated corpus, so editing the live file cannot widen what
    # the agent is permitted to do. Variants are static definitions; only
    # defaultVariant changes at runtime.
    for document in ingest_runbooks():
        for action, sink in (
            (document.metadata.fix, fixes),
            (document.metadata.rollback, rollbacks),
        ):
            if action is None or action.action != "set_flag":
                continue
            flag = action.args.get("flag")
            variant = action.args.get("variant")
            if isinstance(flag, str) and isinstance(variant, str):
                sink.add((flag, variant))
    return frozenset(fixes), frozenset(rollbacks)


def _apply(flag: str, variant: str, *, kind: str) -> dict[str, Any]:
    fixes, rollbacks = _allowed()
    allowed = fixes if kind == "fix" else rollbacks
    if (flag, variant) not in allowed:
        offer = sorted(v for f, v in allowed if f == flag)
        detail = f"; this tool accepts {offer} for {flag!r}" if offer else ""
        raise ActionNotAllowed(
            f"no runbook names {variant!r} as a {kind} for flag {flag!r}{detail}"
        )

    path = _flag_path()
    document = read_document(path)
    previous = current_variant(document, flag)
    updated, changed = set_variant(document, flag, variant)
    if changed:
        write_document(path, updated)
    return {
        "flag": flag,
        "variant": variant,
        "previous": previous,
        "changed": changed,
        "kind": kind,
    }


def set_flag(flag: str, variant: str) -> dict[str, Any]:
    return _apply(flag, variant, kind="fix")


def rollback_flag(flag: str, variant: str) -> dict[str, Any]:
    return _apply(flag, variant, kind="rollback")


def build_server() -> MCPServer:
    """Register the two write tools.

    Both are annotated `read_only_hint=False, destructive_hint=True`, which is
    what `registry._is_write` reads and what keeps them out of every diagnosis
    registry. Marking is not optional here: `_is_write` fails closed, so an
    unannotated tool would also be treated as a write -- but a write server that
    does not say so cannot be checked by anything.
    """
    # Build the allowlist NOW. A corpus that will not ingest, or a runbook with
    # a fix flagd does not recognise, must stop this server from starting rather
    # than surface on the one call that matters.
    # Both of the server's inputs, proven readable before the port binds: the
    # corpus (with its committed-baseline validation) and the live document this
    # server will actually write.
    fixes, rollbacks = _allowed()
    read_document(_flag_path())

    server = MCPServer(
        name=SERVER_NAME,
        instructions=(
            "THE ONLY WRITE SERVER. Both tools change the running system and both "
            f"require human approval. {len(fixes)} fix and {len(rollbacks)} rollback "
            "(flag, variant) pairs are accepted, taken from the runbook corpus; "
            "anything else is refused. rollback_flag RE-ARMS a fault."
        ),
    )
    write = ToolAnnotations(read_only_hint=False, destructive_hint=True)

    server.tool(name="set_flag", description=SET_FLAG_DESCRIPTION, annotations=write)(set_flag)
    server.tool(name="rollback_flag", description=ROLLBACK_DESCRIPTION, annotations=write)(
        rollback_flag
    )
    return server
