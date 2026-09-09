# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE=python:3.12.10-slim-bookworm
FROM ${PYTHON_IMAGE} AS builder
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.10
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-cache

FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONUTF8=1 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" APP_DATA_DIR=/data
RUN groupadd --gid 10001 happyday \
    && useradd --uid 10001 --gid happyday --create-home happyday \
    && mkdir /data && chown happyday:happyday /data
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY app ./app
COPY templates ./templates
COPY static ./static
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os,urllib.request,urllib.parse; host=urllib.parse.urlsplit(os.getenv('APP_BASE_URL','http://localhost')).hostname; request=urllib.request.Request('http://127.0.0.1:8000/health/ready',headers={'Host':host}); urllib.request.urlopen(request,timeout=3)"
CMD ["python", "-m", "uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers", "--no-access-log"]
