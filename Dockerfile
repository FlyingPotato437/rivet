# syntax=docker/dockerfile:1
FROM python:3.13-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /uvx /bin/
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    PATH="/app/.venv/bin:$PATH" \
    RIVET_ENV=production \
    RIVET_DATA_DIR=/var/lib/rivet
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project && \
    python -m playwright install --with-deps chromium && \
    rm -rf /var/lib/apt/lists/* /root/.cache
RUN groupadd --gid 10001 rivet && \
    useradd --uid 10001 --gid 10001 --create-home rivet && \
    mkdir -p /var/lib/rivet/blobs && chown -R rivet:rivet /var/lib/rivet
COPY backend ./backend
COPY migrations ./migrations
COPY alembic.ini ./
COPY scripts/container_entrypoint.py ./scripts/container_entrypoint.py
USER rivet
EXPOSE 8787
ENTRYPOINT ["python", "-m", "scripts.container_entrypoint"]
CMD ["api"]
