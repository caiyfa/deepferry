# ── Build stage ───────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends --fix-missing gcc libc6-dev && rm -rf /var/lib/apt/lists/*
RUN pip install uv

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN uv sync --no-dev

# ── Runtime stage ──────────────────────────────────────────────────────
FROM python:3.12-slim

RUN useradd --create-home --shell /bin/bash deepferry
USER deepferry
WORKDIR /home/deepferry

COPY --from=builder /app/.venv /home/deepferry/.venv
ENV PATH="/home/deepferry/.venv/bin:$PATH"

COPY src/ /home/deepferry/src/

ENV PYTHONPATH=/home/deepferry/src

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

ENTRYPOINT ["python", "-m", "deepferry.cli"]
CMD ["mcp-server", "--transport", "http", "--host", "0.0.0.0", "--port", "8000", "--config", "/home/deepferry/config.toml"]
