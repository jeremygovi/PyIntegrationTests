# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.12
FROM alpine/helm:3.19.0@sha256:aef9b56f64e866207d9591d0abd8f6d767b36aadd12edf68f8a719716d9d29c9 AS helm
FROM python:${PYTHON_VERSION}-slim-bookworm AS base
COPY --from=helm /usr/bin/helm /usr/local/bin/helm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

FROM base AS tooling
ARG UV_VERSION=0.8.22
RUN pip install --no-cache-dir uv==${UV_VERSION}
ENV UV_CACHE_DIR=/tmp/uv-cache

FROM tooling AS dev
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv export --quiet --frozen --all-extras --group dev --no-emit-project -o /tmp/requirements.txt \
    && uv pip install --system --no-cache -r /tmp/requirements.txt \
    && uv pip install --system --no-cache --no-deps --editable .
COPY . .
CMD ["python", "-m", "pytest"]

FROM tooling AS runtime
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv export --quiet --frozen --all-extras --no-dev --no-emit-project -o /tmp/requirements.txt \
    && uv pip install --system --no-cache -r /tmp/requirements.txt \
    && uv pip install --system --no-cache --no-deps .
RUN useradd --create-home --uid 10001 runner
USER runner
WORKDIR /work
ENTRYPOINT ["pyintegrationtests"]
CMD ["--help"]

FROM tooling AS minimal
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv export --quiet --frozen --no-dev --no-emit-project -o /tmp/requirements.txt \
    && uv pip install --system --no-cache -r /tmp/requirements.txt \
    && uv pip install --system --no-cache --no-deps .
CMD ["pyintegrationtests", "--help"]
