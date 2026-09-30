# Multi-stage Dockerfile for Self-Healing LLM Gateway
# Stage 1: Build stage with uv
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Install dependencies into virtual environment
COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-install-project --no-dev

# Copy application, alembic files, and README for project installation
COPY README.md /app/
COPY app/ /app/app/
COPY alembic/ /app/alembic/
COPY alembic.ini /app/alembic.ini
RUN uv sync --frozen --no-dev


# Stage 2: Minimal runtime image
FROM python:3.12-slim-bookworm AS runtime

WORKDIR /app

# Set production environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000

# Create non-root system user and group
RUN groupadd -r appgroup && useradd -r -g appgroup -d /app -s /sbin/nologin appuser

# Copy virtual environment and application code from builder
COPY --from=builder --chown=appuser:appgroup /app/.venv /app/.venv
COPY --chown=appuser:appgroup app/ /app/app/
COPY --chown=appuser:appgroup alembic/ /app/alembic/
COPY --chown=appuser:appgroup alembic.ini pyproject.toml /app/

# Switch to non-root user
USER appuser

EXPOSE 8000

# Startup command runs database migrations then starts Uvicorn
CMD ["sh", "-c", "python -m alembic upgrade head && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"]
