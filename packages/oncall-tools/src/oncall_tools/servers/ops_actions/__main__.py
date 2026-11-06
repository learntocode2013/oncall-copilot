"""`python -m oncall_tools.servers.ops_actions --transport ... --port N`."""

from __future__ import annotations

import sys

from oncall_tools.servers._serve import run
from oncall_tools.servers.ops_actions import build_server


def main(argv: list[str] | None = None) -> int:
    # build_server reads the corpus and the flag file, so a bad corpus or an
    # unreadable flagd document fails here -- before the transport binds a port
    # and the service starts reporting healthy.
    return run(build_server(), sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
