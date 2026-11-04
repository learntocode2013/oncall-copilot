"""`python -m oncall_tools.servers.runbooks_kb [--lexical-only] --transport ... --port N`.

`--lexical-only` is parsed here rather than in `_serve`, which is shared by all
four servers and where a retrieval flag would mean nothing to the other three.
"""

from __future__ import annotations

import argparse
import sys

from oncall_tools.servers._serve import run
from oncall_tools.servers.runbooks_kb import build_server


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--lexical-only",
        action="store_true",
        help=(
            "serve the BM25 baseline instead of the hybrid retriever. Without this "
            "the server refuses to start when sentence-transformers is absent, "
            "rather than quietly serving the baseline."
        ),
    )
    args, rest = parser.parse_known_args(argv)

    if "--help" in rest or "-h" in rest:
        parser.print_help()
        print()

    # build_server resolves the retriever, so an absent extra fails here --
    # before the transport binds a port and looks healthy.
    return run(build_server(semantic=not args.lexical_only), rest)


if __name__ == "__main__":
    raise SystemExit(main())
