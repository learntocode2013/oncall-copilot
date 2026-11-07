"""Connect the registry to real servers running in this process.

No port, no subprocess, and `semantic=False` so no torch -- which is what puts
the registry under the CI `test` job rather than only under the container check.
"""

from mcp.client import Client
from oncall_tools.servers.ops_actions import build_server as ops_server
from oncall_tools.servers.runbooks_kb import build_server as runbooks_server
from oncall_tools.servers.telemetry import build_server as telemetry_server

#: Resolves to three endpoints and connects to none of them. `.invalid` is
#: reserved by RFC 2606 and cannot resolve, so a test that accidentally calls a
#: telemetry tool fails loudly instead of reaching something real.
TELEMETRY_HOST = "telemetry.invalid"


def in_process(server):
    """`connect` factory for `load_tools`, dispatching on the server name."""
    if server.name == "runbooks":
        return Client(runbooks_server(semantic=False))
    if server.name == "ops_actions":
        return Client(ops_server())
    if server.name == "telemetry":
        return Client(telemetry_server(host=TELEMETRY_HOST))
    raise AssertionError(f"no in-process client for {server.name!r}")
