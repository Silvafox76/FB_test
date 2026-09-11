# One image, two commands: the pipeline runs the CLI, the review app runs uvicorn.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/usr/local

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml README.md ./
RUN uv sync --no-dev --no-install-project

COPY monitor/ ./monitor/
COPY review/ ./review/
COPY migrations/ ./migrations/

# No inbound path is opened by the image itself; compose publishes 8080 on loopback.
CMD ["python", "-m", "monitor.cli", "status"]
