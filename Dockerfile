# syntax=docker/dockerfile:1.7
# Stage 1: resolve locked dependencies into a venv with uv.
# Stage 2: copy venv into a slim runtime (no uv or build tooling shipped).
FROM ghcr.io/astral-sh/uv:0.11.16-python3.12-trixie-slim AS builder

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src

RUN uv sync --frozen --no-editable


FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 10001 mcp
USER mcp
WORKDIR /home/mcp

COPY --from=builder --chown=mcp:mcp /opt/venv /opt/venv

# stdio transport — clients launch this container and talk JSON-RPC over fds 0/1.
ENTRYPOINT ["infra-mcp", "run"]
