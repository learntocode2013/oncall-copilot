"""Connect the registry to real servers running in this process.

No port, no subprocess, and `semantic=False` so no torch -- which is what puts
the registry under the CI `test` job rather than only under the container check.
"""

from mcp.client import Client
from oncall_tools.servers.ops_actions import build_server as ops_server
from oncall_tools.servers.runbooks_kb import build_server as runbooks_server


def in_process(server):
    """`connect` factory for `load_tools`, dispatching on the server name."""
    if server.name == "runbooks":
        return Client(runbooks_server(semantic=False))
    if server.name == "ops_actions":
        return Client(ops_server())
    raise AssertionError(f"no in-process client for {server.name!r}")
