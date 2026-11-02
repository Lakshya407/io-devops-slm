# Ephemeral task image used by the sandbox runner.
#
# It contains only language runtimes and test tooling. It has no Docker
# socket, no credentials, and no network access by default when started.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pytest==8.3.4 pytest-asyncio==0.25.0

RUN groupadd --gid 10001 sandbox \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin sandbox

WORKDIR /workspace
USER 10001:10001

# The runner overrides the entrypoint with the validated argv it receives.
CMD ["python", "-c", "print('io-agent-sandbox ready')"]
