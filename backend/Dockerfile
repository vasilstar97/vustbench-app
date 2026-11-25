FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VUSTBENCH_DATA_DIR=/app/data \
    VUSTBENCH_ARTIFACTS_DIR=/app/artifacts \
    OVERPASS_CACHE_DIR=/app/artifacts/cache/iduedu \
    OVERPASS_TIMEOUT=120 \
    OVERPASS_MAX_RETRIES=1

WORKDIR /app
COPY constraints.txt /app/constraints.txt
COPY packages/vustbench-core /app/packages/vustbench-core
COPY backend /app/backend
RUN pip install -c /app/constraints.txt /app/packages/vustbench-core /app/backend \
    && useradd --create-home --uid 10001 app \
    && mkdir -p /app/artifacts \
    && chown app:app /app/artifacts

USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=30m --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"
CMD ["python", "-m", "vustbench_app", "serve", "--prepare"]
