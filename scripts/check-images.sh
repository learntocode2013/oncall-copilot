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
# All five checks passed over both.
#
# THE RULE FOR WHAT GETS RUN: run what needs no mounts, build what does.
# Replicating a service's compose configuration here would create a second copy
# of compose.oncall.yaml that drifts from the first, and a smoke test against a
# configuration nobody ships is worse than no smoke test.

set -euo pipefail

ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
cd "$ROOT"

# ci- prefixed and publishing no host ports, so this is safe to run while a
# developer has the real stack up. Every probe goes through `docker exec`.
RUNBOOKS=ci-mcp-runbooks
SERVICE=ci-oncall-service

cleanup() { docker rm -f "$RUNBOOKS" "$SERVICE" >/dev/null 2>&1 || true; }
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

# oncall-lab is build-only: its runtime needs eval/faults/manifest.yaml mounted
# at /lab/manifest.yaml and deploy/flagd at /etc/flagd. Those bindings belong to
# compose. See the rule at the top.

printf '\nall images built; mcp-runbooks and oncall-service start and answer\n'
