# mcp-runbooks: read-only runbook search over the authored corpus.
#
# Build context is the REPO ROOT, not this directory -- uv needs the workspace
# root pyproject.toml and uv.lock to resolve a member, and `oncall-tools`
# depends on `oncall-knowledge` as a workspace source.
#
# IMAGE SIZE: the semantic extra pulls torch. That is deliberate -- config B
# (BM25 + MiniLM fused by RRF) is the graded configuration, and there is no
# silent fallback to the lexical baseline. To trade retrieval quality for a
# much smaller image, override the command with --lexical-only; the server then
# says LEXICAL BASELINE ONLY in its MCP instructions so no client can mistake
# one for the other.
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
COPY packages/oncall-graph/pyproject.toml packages/oncall-graph/

COPY packages/oncall-contracts/ packages/oncall-contracts/
COPY packages/oncall-knowledge/ packages/oncall-knowledge/
COPY packages/oncall-tools/ packages/oncall-tools/
COPY packages/oncall-graph/ packages/oncall-graph/

# --frozen: the lockfile is the contract, and a container that silently
# re-resolves is a container running untested versions.
RUN uv sync --frozen --no-dev --extra semantic

# The corpus is read at startup by ingest_runbooks(), which pins its root to
# data/runbooks/ and therefore cannot reach eval/.
COPY data/ data/

# Bake the model into the image at its pinned revision. Without this the
# container needs the network on every cold start and re-downloads ~80 MB per
# recreate -- and a model fetched at runtime is a model nobody pinned.
RUN uv run python -c "\
from oncall_knowledge.indexing.semantic import SemanticIndex; \
from sentence_transformers import SentenceTransformer; \
SentenceTransformer(SemanticIndex.MODEL_ID, revision=SemanticIndex.MODEL_REVISION)"

EXPOSE 8102

# 0.0.0.0, not the SDK's loopback default: a server bound to 127.0.0.1 inside a
# container is unreachable from the Compose network, and the failure mode is a
# container that looks healthy and answers nobody.
CMD ["uv", "run", "python", "-m", "oncall_tools.servers.runbooks_kb", \
     "--transport", "http", "--port", "8102", "--host", "0.0.0.0"]
