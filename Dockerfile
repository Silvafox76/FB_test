# One image, two commands: the pipeline runs the CLI, the review app runs uvicorn.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/usr/local

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# uv.lock is copied and --frozen is passed so the image installs the versions that
# were tested rather than re-resolving. Without the lock, `uv sync` resolves fresh at
# build time and the image can quietly carry different dependency versions from the
# ones the suite ran against - which is the whole point of committing a lock file.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY monitor/ ./monitor/
COPY review/ ./review/
COPY migrations/ ./migrations/

# No inbound path is opened by the image itself; compose publishes 8080 on loopback.
CMD ["python", "-m", "monitor.cli", "status"]
