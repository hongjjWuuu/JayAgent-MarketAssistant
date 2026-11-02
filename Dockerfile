FROM python:3.12-slim-bookworm AS builder
WORKDIR /build
COPY pyproject.toml alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY simulator ./simulator
COPY scripts ./scripts
RUN python -m pip install --no-cache-dir --upgrade "pip>=26.2.2" "setuptools>=78.1.1" \
    && python -m pip install --no-cache-dir --no-build-isolation --target /opt/runtime .

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/runtime PATH=/opt/runtime/bin:$PATH
WORKDIR /app
COPY --from=builder /opt/runtime /opt/runtime
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY simulator ./simulator
COPY scripts ./scripts
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
