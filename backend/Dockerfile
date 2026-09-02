# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# Multi-stage build for the DecisionLedger Decision Automation Platform (spec §15).
# Stage 1 builds wheels; the final stage carries only the runtime, kept slim.
# ---------------------------------------------------------------------------

FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY requirements.txt .
# Build wheels once so the runtime stage installs without a compiler toolchain.
RUN pip wheel --wheel-dir /wheels -r requirements.txt


FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Non-root runtime user (spec §13: least privilege).
RUN useradd --create-home --uid 10001 decisionledger

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt && rm -rf /wheels

# Application code and rulesets.
COPY app ./app
COPY rulesets ./rulesets

USER decisionledger

EXPOSE 8000

# Container-level liveness probe hits the app's own health endpoint.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else sys.exit(1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
