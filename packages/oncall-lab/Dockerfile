FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY packages ./packages

RUN uv sync --frozen --no-dev --package oncall-lab

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8020

CMD ["python", "-m", "oncall_lab"]
