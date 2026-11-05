"""One argument parser for every MCP server, so all four run both transports.

    MCP_TRANSPORT=stdio   development: the graph spawns each server as a subprocess
    MCP_TRANSPORT=http    Docker: each server is its own container

`registry.py` reads the transport from config, so `build_graph(tools=load_tools())`
is identical either way and P2 never knows which one is live.

`http` maps to the SDK's `streamable-http`. The flag stays `http` because that is
what the Compose overlay and the spec call it.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from mcp.server.mcpserver import MCPServer

DEFAULT_HOST = "127.0.0.1"


def run(server: MCPServer, argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=f"oncall_tools.servers.{server.name}")
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument(
        "--port",
        type=int,
        help="required for --transport http; ports 8101-8104 are ours (8080 etc. are the demo's)",
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    args = parser.parse_args(argv)

    if args.transport == "stdio":
        server.run(transport="stdio")
        return 0

    if args.port is None:
        parser.error("--transport http requires --port")

    # 0.0.0.0 rather than the SDK's loopback default when containerised: a
    # server bound to 127.0.0.1 inside a container is unreachable from the
    # Compose network, and the failure looks like the server never started.
    server.run(transport="streamable-http", host=args.host, port=args.port)
    return 0
