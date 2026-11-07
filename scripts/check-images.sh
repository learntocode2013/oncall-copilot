#!/usr/bin/env bash
# Build every shipped container image, and start the ones that can start alone.
#
# WHY THIS EXISTS: nothing else in CI builds an image, and twice that let a
# green pipeline sit on top of a container that could not run --
#
#   1. mcp-runbooks would not BUILD. `uv sync` without `--package` syncs the
#      root project, which depends on all seven workspace members, so it needs
#      every member's source in the build context. PR #11 turned oncall-eval,
#      oncall-lab and oncall-service into real distributions and the next build
#      failed with "Distribution not found at file:///app/packages/oncall-eval".
#
#   2. mcp-runbooks would not START. ingest_runbooks() validates every
#      runbook's `fix` against deploy/flagd/demo.flagd.json, which the image
#      never copied.
#
#   3. oncall-lab would not IMPORT. #19 moved the flagd writer into
#      oncall_contracts and the lab began importing it, but oncall-lab's
#      [project.dependencies] never said so. `uv sync --package oncall-lab`
#      installs only what the member declares, so the image built clean and
#      exited at startup with ModuleNotFoundError. This was build-only under
#      the rule below, so nothing ran it.
#
# All five checks passed over all three.
#
# THE RULE FOR WHAT GETS RUN: run what needs no mounts, build what does.
# Replicating a service's compose configuration here would create a second copy
# of compose.oncall.yaml that drifts from the first, and a smoke test against a
# configuration nobody ships is worse than no smoke test.
#
# BUT A BUILD-ONLY IMAGE STILL GETS ITS ENTRY POINT IMPORTED. Failure 3 needed
# no configuration to find: the module does not import, whatever it is mounted
# on. An import check needs no env, no ports and no mounts, so it cannot drift
# from compose -- it is not a configuration. It catches the whole class of
# "declared nowhere, installed nowhere, built fine".

set -euo pipefail

ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
cd "$ROOT"

# ci- prefixed and publishing no host ports, so this is safe to run while a
# developer has the real stack up. Every probe goes through `docker exec`.
RUNBOOKS=ci-mcp-runbooks
SERVICE=ci-oncall-service
TELEMETRY=ci-mcp-telemetry

cleanup() { docker rm -f "$RUNBOOKS" "$SERVICE" "$TELEMETRY" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup

build() {
    local name=$1 dockerfile=$2
    printf '\n=== build %s ===\n' "$name"
    docker build -f "$dockerfile" -t "$name:ci" .
    printf '%s:ci  %s\n' "$name" "$(docker images --format '{{.Size}}' "$name:ci")"
}

build oncall-service packages/oncall-service/Dockerfile
build oncall-lab     packages/oncall-lab/Dockerfile
build mcp-runbooks   packages/oncall-tools/Dockerfile
build mcp-ops-actions packages/oncall-tools/Dockerfile.ops-actions
build mcp-telemetry   packages/oncall-tools/Dockerfile.telemetry

# --- waiting -----------------------------------------------------------------

# Polls a probe inside a container until it succeeds, and fails fast if the
# container exits first -- which is the interesting case: a container that dies
# during startup would otherwise just time out and report nothing useful.
wait_for() {
    local container=$1 attempts=$2 what=$3
    shift 3
    for _ in $(seq 1 "$attempts"); do
        if docker exec "$container" "$@" >/dev/null 2>&1; then
            return 0
        fi
        if [ "$(docker inspect --format '{{.State.Status}}' "$container")" != running ]; then
            echo "FAIL: $container exited before $what" >&2
            docker logs "$container" 2>&1 | tail -30 >&2
            exit 1
        fi
        sleep 1
    done
    echo "FAIL: $container timed out $what" >&2
    docker logs "$container" 2>&1 | tail -30 >&2
    exit 1
}

# --- mcp-runbooks: self-contained, so it gets the full contract check --------

printf '\n=== run mcp-runbooks ===\n'
docker run -d --name "$RUNBOOKS" mcp-runbooks:ci >/dev/null

# 60 attempts, and deliberately generous: the retriever ingests the corpus,
# loads the model and embeds 18 documents BEFORE the port is bound, which is
# what makes a bound port mean something. Compose allows the same 60s.
wait_for "$RUNBOOKS" 60 "binding port 8102" bash -c 'echo > /dev/tcp/localhost/8102'

docker cp scripts/mcp_smoke.py "$RUNBOOKS:/tmp/mcp_smoke.py" >/dev/null
if ! docker exec "$RUNBOOKS" python /tmp/mcp_smoke.py; then
    docker logs "$RUNBOOKS" 2>&1 | tail -30 >&2
    exit 1
fi

# --- oncall-service: one env var, no mounts, so it gets a health probe ------

printf '\n=== run oncall-service ===\n'
# 0.0.0.0 because the app defaults to loopback and compose supplies this. A
# container bound to 127.0.0.1 is unreachable from the Compose network, and the
# failure looks like a server that never started.
docker run -d --name "$SERVICE" -e ONCALL_SERVICE_HOST=0.0.0.0 oncall-service:ci >/dev/null

wait_for "$SERVICE" 30 "answering /healthz" python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=2)"
echo "oncall-service: healthz OK"

# --- mcp-telemetry: no mounts, so it gets run and its contract checked ------
#
# Pointed at 127.0.0.1:1, which is not listening. That is deliberate and it is
# the configuration worth testing: this server resolves its three endpoints
# while building and connects to nothing, so it MUST bind its port with every
# backend down, and then name the dead one per query. A server that refused to
# start here would be unable to report a telemetry outage during an incident.
printf '\n=== run mcp-telemetry ===\n'
docker run -d --name "$TELEMETRY" \
    -e PROMETHEUS_URL=http://127.0.0.1:1 \
    -e JAEGER_URL=http://127.0.0.1:1 \
    -e OPENSEARCH_URL=http://127.0.0.1:1 \
    mcp-telemetry:ci >/dev/null

# 15 attempts: no model, no corpus. Resolving three URLs and binding a port.
wait_for "$TELEMETRY" 15 "binding port 8101" bash -c 'echo > /dev/tcp/localhost/8101'

docker cp scripts/telemetry_smoke.py "$TELEMETRY:/tmp/telemetry_smoke.py" >/dev/null
if ! docker exec "$TELEMETRY" python /tmp/telemetry_smoke.py; then
    docker logs "$TELEMETRY" 2>&1 | tail -30 >&2
    exit 1
fi

# THE NEGATIVE CHECK, which is the one a bound port can never give you. With no
# endpoints configured this image must EXIT, not start and answer every query
# against an address nobody owns. It is the whole reason the client lost its
# default host, and a container is where the wrong default used to hide: these
# three variables were always set, so nothing ever exercised their absence.
printf '\n=== mcp-telemetry refuses to start unconfigured ===\n'
if docker run --rm --name "$TELEMETRY-unset" mcp-telemetry:ci >/dev/null 2>&1; then
    echo "FAIL: mcp-telemetry started with no endpoints configured" >&2
    exit 1
fi
unconfigured=$(docker run --rm mcp-telemetry:ci 2>&1 || true)
for variable in PROMETHEUS_URL JAEGER_URL OPENSEARCH_URL; do
    case "$unconfigured" in
        *"$variable"*) ;;
        *) echo "FAIL: the refusal does not name $variable: $unconfigured" >&2; exit 1 ;;
    esac
done
echo "mcp-telemetry: refuses to start, naming all three variables"

# --- build-only images: import the entry point ------------------------------
#
# oncall-lab's runtime needs eval/faults/manifest.yaml at /lab/manifest.yaml,
# and ops-actions needs deploy/flagd mounted READ-WRITE at /etc/flagd -- it
# exists to edit that document. Those bindings belong to compose, so neither is
# started here. Importing the module needs none of them, and it is what would
# have caught the lab shipping without oncall-contracts installed.
#
# ops-actions' startup path beyond the import is covered by test_ops_actions.py,
# which builds the server against a tmp_path copy of the real flagd document.

imports() {
    local name=$1 module=$2
    printf '\n=== import %s ===\n' "$name"
    if ! docker run --rm --entrypoint python "$name:ci" -c "import $module" 2>&1; then
        echo "FAIL: $name cannot import $module -- the image built but will not start" >&2
        exit 1
    fi
    echo "$name: $module imports OK"
}

imports oncall-lab      oncall_lab.main
imports mcp-ops-actions oncall_tools.servers.ops_actions

printf '\nall images built; 3 start and answer, 2 import their entry point\n'
