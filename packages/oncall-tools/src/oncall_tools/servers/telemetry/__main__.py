"""`python -m oncall_tools.servers.telemetry --transport ... --port 8101`.

`--host` is `_serve`'s BIND address, which is why the telemetry host is a
`--telemetry-host` flag and not `--host`: two different meanings for one word
on one command line is how a server ends up bound to a Prometheus address.
"""

from __future__ import annotations

import argparse
import sys

from oncall_tools.servers._serve import run
from oncall_tools.servers.telemetry import build_server


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--telemetry-host",
        default=None,
        help=(
            "one host serving Prometheus (9090), Jaeger (16686) and OpenSearch (9200). "
            "A shorthand for the three URL variables; it does not bind anything. "
            "Without it, PROMETHEUS_URL, JAEGER_URL and OPENSEARCH_URL must all be set."
        ),
    )
    parser.add_argument(
        "--window-minutes",
        type=int,
        default=5,
        help="how far back all three tools look (default: 5)",
    )
    args, rest = parser.parse_known_args(argv)

    if "--help" in rest or "-h" in rest:
        parser.print_help()
        print()

    # build_server resolves the endpoints, so an unconfigured server fails HERE
    # -- before the transport binds a port and starts looking healthy.
    server = build_server(host=args.telemetry_host, minutes=args.window_minutes)
    return run(server, rest)


if __name__ == "__main__":
    raise SystemExit(main())
