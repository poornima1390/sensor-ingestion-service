# Build stage: install dependencies into a self-contained virtualenv.
FROM python:3.12-slim AS builder

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Runtime stage: copy only the built venv and the application source, so build
# tooling and pip caches never reach the published image.
FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Run as an unprivileged user; a container compromise then starts without root.
RUN useradd --create-home --uid 10001 appuser

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY app ./app

# The runtime user needs somewhere writable for the local SQLite fallback.
# Only this directory is writable; the application source stays root-owned.
RUN mkdir -p /app/.data && chown appuser:appuser /app/.data

USER appuser

EXPOSE 8080

# `exec` matters: it replaces the shell so uvicorn becomes PID 1 and receives
# SIGTERM directly. Without it the shell swallows the signal, the platform waits
# out its grace period, and shutdown is a kill instead of a drain.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
