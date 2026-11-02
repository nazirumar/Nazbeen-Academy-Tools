# TA Platform backend — production-style image (Python 3.12 slim).
# Migrations run automatically at container start (alembic upgrade head).
#
# Phase 9 hardening:
# - dependencies installed reproducibly (no cache, requirements pinned in requirements.txt)
# - runs as a non-root user
# - built-in liveness healthcheck (no curl needed)
# - no secrets are copied into the image: configuration comes from env at runtime
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first (better layer caching).
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Application + migrations.
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
COPY app ./app

# Non-root runtime user (no shell access needed by the app).
RUN groupadd --system ta && useradd --system --gid ta --create-home ta
USER ta

EXPOSE 8000

# Built-in liveness check (no curl in slim images).
HEALTHCHECK --interval=15s --timeout=5s --start-period=25s --retries=5 \
  CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=4).status==200 else 1)"

# Migrations are idempotent; they run before the server on every start.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
