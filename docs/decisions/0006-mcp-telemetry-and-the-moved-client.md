# ADR-0006: `mcp-telemetry`, and what moving its client had to change

**Status:** Accepted
**Date:** 2026-11-07
**Deciders:** P1 (domain, knowledge & memory). **This moves ~150 lines out of P2's package and changes two of `LiveShop`'s behaviours. P2 should read §2 and §3. It also records a defect in `handoffs.py` that is P2's to fix — §5.**
**Builds on:** [ADR-0003](0003-mcp-tool-registry-and-write-isolation.md) (the registry and the four-server plan), [ADR-0004](0004-the-only-write-server.md) (the `ToolError` lesson).

## Context

Three of the four planned MCP servers now exist: `runbooks` (8102), `ops_actions` (8104) and, with this change, `telemetry` (8101). `tickets` (8103) is still unwritten.

Telemetry was not a new server so much as a relocation. `oncall_graph/astronomy_shop.py` already queried Prometheus, OpenSearch and Jaeger, already truncated its results to MCP-sized payloads (15 series, 20 lines of 240 chars, 20 trace ids), and already had tests. What it did not have was a server, a container, or a place in the registry.

**The move was forced, not chosen.** `ALLOWED_IMPORTS` permits `oncall_tools` → `{oncall_contracts, oncall_knowledge}` and nothing else, so a telemetry server could not import P2's class to wrap it. The alternative — copying the logic into the server — is precisely the shape ADR-0005 was written about: two definitions in two owners' packages, both tested, neither able to fail. `oncall_graph` → `oncall_tools` *is* permitted, so the client moves and `LiveShop` reads it back.

## Decisions

### 1. The client lives with the server; `LiveShop` stays with P2

`oncall_tools/servers/telemetry/client.py` holds `AstronomyShop`. `oncall_graph/astronomy_shop.py` keeps `LiveShop` — the scripted shop with its three reads pointed at the real thing — and re-exports `AstronomyShop` so existing imports resolve.

`oncall-graph` therefore declares `oncall-tools` in `[project.dependencies]`. The guard added in #22, `test_every_package_declares_the_workspace_members_it_imports`, is what makes that a requirement rather than a thing to remember.

### 2. No default host, and the server refuses to start without one

The old module defaulted to `http://10.241.2.69:9090`, Jaeger on `32797`, OpenSearch on `32794`. Those are NodePorts of a Kubernetes cluster that is not this stack — not just the host was wrong, the ports were too.

It was invisible, and for the usual reason: `deploy/compose.oncall.yaml` has always set all three URLs on `oncall-service`, so every containerised path overrode the default and only a developer running `solo_demo.py` from the host ever reached that address. A wrong default that is always overridden is not harmless; it is a wrong default waiting for the one path that does not override it.

So the three URLs are required, and the server resolves them in `build_server` — before `_serve.run` binds a port. `ASTRONOMY_SHOP_HOST` survives as a one-variable shorthand, because `solo_demo` calls `LiveShop()` with no arguments and asking for three variables where one will do buys no safety. It is a shorthand, not a default: unset, nothing is assumed.

This follows `runbooks_kb.configure(semantic=...)`, which refuses to fall back to the lexical baseline for the same reason — a server that quietly serves the wrong thing is worse than one that will not start.

**Measured, and worth recording.** Removing `env=(...)` from the registry spec makes the spawned server die with `TelemetryNotConfigured` naming all three variables. The identical gap in `ops_actions` (#20) produced a *silent write to the wrong file* reported as `changed: true`. Refusing to start converted that failure class from silent-wrong into loud-fail.

### 3. An unreachable backend raises; an empty result does not

The old methods caught `URLError` and returned `{"error": "connection refused"}`. An agent handed that dict reads it as telemetry and concludes the metric is quiet. `registry.ToolCallError` already says this outright — *"a failed tool cannot read to the model as a tool that found nothing"* — and this client was the one place still contradicting it.

`TelemetryUnavailable` is raised and the server re-raises it as `ToolError`, so the URL that failed reaches the caller. `series: []` still returns normally: "Prometheus matched nothing" is a real answer, and conflating the two would destroy the distinction this change exists to make.

**This changes `LiveShop`.** `Solo` has no handler for `TelemetryUnavailable`, so a dead backend now stops a live run instead of quietly producing a diagnosis with no evidence behind it. That is the intended trade: an incident the agent could not investigate must not look like an incident it investigated and found nothing.

### 4. The client logs; it does not print

In a stdio server **stdout is the JSON-RPC channel.** The old client traced through `oncall_graph.meter.say`, which is a `print`. Those lines would have interleaved with responses and corrupted the session's framing, and the symptom — a parse error — would have appeared nowhere near the cause.

The client uses a module logger, and `test_the_client_never_writes_to_stdout` pins it. The trace lines the terminal demo shows now come from `LiveShop`, where stdout is harmless.

### 5. Recorded, NOT fixed: the graph cannot read live telemetry

`handoffs.py` extracts evidence by reading `result["error_rate"]`, `result["fault"]` and `result["local_cause"]`. Those three keys are invented by the **scripted** shop in `stubs.py`. The real client returns `series`, `lines`, `total`, `count` and `trace_ids`, and never those three.

So today, with `LiveShop`, the graph queries real Prometheus and real Jaeger, receives real answers, and `observed_error_rate()` returns `None`, `suspects_from_exchanges()` finds nothing from telemetry, and it falls back to the service the alert named. No exception, no empty result, nothing in a log — the live path reads real telemetry and extracts zero evidence from it.

This server does not paper over it. Adding a `local_cause` verdict to a read tool would decide for P2 where interpretation belongs, and a verdict is not a read. The gap is named here, and in the server's own module docstring, so that whoever wires `build_graph(tools=load_tools())` meets it as a known defect rather than as a mystery.

## Consequences

- Three servers in `SERVERS`; `load_tools()` returns **seven** tools, five reads and two writes.
- `READ_ONLY_SERVERS` gains `telemetry`. A tool there that acquires a side effect must leave that set in the same commit.
- `Dockerfile.telemetry` is a third Dockerfile. It copies no `data/` and no `deploy/flagd/`: this server ingests no runbooks and reads no flag document, and baking either in would hand a telemetry reader the corpus that names every fault's root cause.
- `make images` runs it (no mounts, so the script's own rule says run it), checks its contract with `scripts/telemetry_smoke.py` against a backend that is **not listening**, and asserts the image **exits** when unconfigured. That negative check is the one a bound port can never give you.
- `make -C deploy verify` gains the `mcp-telemetry` line, per the overlay's rule that a service and its health check land together.
- Still open: `build_graph` refuses a non-empty tool dict, so nothing in the graph calls these tools yet. That, the third `_as_langchain_tool` branch, and §5 are one piece of work and it is P2's.
