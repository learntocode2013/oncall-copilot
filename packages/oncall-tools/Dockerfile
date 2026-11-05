# mcp-runbooks: read-only runbook search over the authored corpus.
#
# Build context is the REPO ROOT, not this directory -- uv needs the workspace
# root pyproject.toml and uv.lock to resolve a member, and `oncall-tools`
# depends on `oncall-knowledge` as a workspace source.
#
# IMAGE SIZE: 2.19 GB, 1m45s on a cold build (arm64, no Docker or uv cache).
# Measured 2026-11-05. It was 6.42 GB and 6.5 minutes before three changes:
# torch pinned to the CPU index (see the workspace root's [[tool.uv.index]]),
# the sync narrowed to this member, and uv's download cache moved to a mount.
#
# The remaining 1.37 GB venv layer is torch (688 MB), transformers, scipy and
# sklearn. Not yet done, in descending value: a multi-stage build that keeps
# only .venv and drops the uv binary, and deleting torch/test, torch/include
# and torch/bin (~230 MB) -- which would make the installed tree differ from
# the lockfile, so it is a decision, not a cleanup.
#
# torch is here deliberately: config B (BM25 + MiniLM fused by RRF) is the
# graded configuration, and there is no silent fallback to the lexical
# baseline. To trade retrieval quality for a much smaller image, override the
# command with --lexical-only; the server then says LEXICAL BASELINE ONLY in
# its MCP instructions so no client can mistake one for the other.
FROM python:3.12-slim

# Pinned to CI's UV_VERSION. A different resolver here than in CI would mean
# the container runs dependencies nobody tested.
COPY --from=ghcr.io/astral-sh/uv:0.11.17 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Manifests first so a code change does not re-resolve or re-download torch.
COPY pyproject.toml uv.lock ./
COPY packages/oncall-contracts/pyproject.toml packages/oncall-contracts/
COPY packages/oncall-knowledge/pyproject.toml packages/oncall-knowledge/
COPY packages/oncall-tools/pyproject.toml packages/oncall-tools/

COPY packages/oncall-contracts/ packages/oncall-contracts/
COPY packages/oncall-knowledge/ packages/oncall-knowledge/
COPY packages/oncall-tools/ packages/oncall-tools/

# --frozen: the lockfile is the contract, and a container that silently
# re-resolves is a container running untested versions.
#
# --package oncall-tools, NOT the root project. The root depends on all seven
# workspace members, so a root sync requires every member's source present in
# the build context -- which is how this image broke: PR #11 turned
# oncall-eval, oncall-lab and oncall-service into real distributions, and the
# next build failed with "Distribution not found at
# file:///app/packages/oncall-eval". Nothing in CI builds this image, so all
# five checks stayed green over an unbuildable container.
#
# Syncing the member instead installs only what the server imports, and keeps
# langgraph, fastapi and the eval harness out of an MCP server container.
#
# The cache mount keeps uv's downloaded wheels OUT of the layer. Measured:
# without it the venv is 1.3 GB but its layer is 2.45 GB, because
# UV_LINK_MODE=copy copies each wheel into .venv and leaves the original in
# /root/.cache/uv inside the same layer. It also means a teammate's rebuild
# re-uses the wheels instead of re-downloading torch.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package oncall-tools --extra semantic

# Put the venv on PATH and stop using `uv run` past this point. `uv run`
# re-targets the ROOT project, which is not fully present in this context, so
# it fails with "Failed to parse entry: oncall-eval" -- at build time for the
# model bake, and it would have failed the same way on every container start.
# uv is a build-time tool; nothing should resolve a project at runtime.
ENV PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV=/app/.venv

# The corpus is read at startup by ingest_runbooks(), which pins its root to
# data/runbooks/ and therefore cannot reach eval/.
COPY data/ data/

# ingest_runbooks() also validates every runbook's `fix` against the demo's
# flag definitions -- a fix naming a flag or variant the demo does not have is
# a fix the remediation tool cannot apply. Without this file the container
# raises IngestionError at startup and exits. Found by running it: the eager
# warm-up in build_server turned what would have been a mid-incident failure
# into a container that refuses to start.
COPY deploy/flagd/ deploy/flagd/

# Bake the model into the image at its pinned revision. Without this the
# container needs the network on every cold start and re-downloads ~80 MB per
# recreate -- and a model fetched at runtime is a model nobody pinned.
RUN python -c "\
from oncall_knowledge.indexing.semantic import SemanticIndex; \
from sentence_transformers import SentenceTransformer; \
SentenceTransformer(SemanticIndex.MODEL_ID, revision=SemanticIndex.MODEL_REVISION)"

EXPOSE 8102

# 0.0.0.0, not the SDK's loopback default: a server bound to 127.0.0.1 inside a
# container is unreachable from the Compose network, and the failure mode is a
# container that looks healthy and answers nobody.
CMD ["python", "-m", "oncall_tools.servers.runbooks_kb", \
     "--transport", "http", "--port", "8102", "--host", "0.0.0.0"]
