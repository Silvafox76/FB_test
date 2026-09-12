# One image, two commands: the pipeline runs the CLI, the review app runs uvicorn.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/usr/local

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# poppler-utils, for `pdftotext`. Burkina Faso publishes its notices as a weekly PDF
# bulletin and `monitor/normalise/ocr.py` measures the text layer before deciding
# whether the issue needs OCR at all (BUILD_ORDER step 18). It is a system binary and
# not a Python dependency, so it belongs here rather than in pyproject.toml. Without
# it every bulletin measures as a scan and is billed to Textract.
RUN apt-get update \
 && apt-get install -y --no-install-recommends poppler-utils \
 && rm -rf /var/lib/apt/lists/*

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
