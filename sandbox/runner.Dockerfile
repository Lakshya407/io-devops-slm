# Sandbox runner: the only component that receives the Docker socket.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY runner.py .
RUN groupadd --gid 10001 runner \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin runner

# The runner needs the Docker socket, which is normally root:docker. It runs as
# root inside its own small container; no workspace code executes here.
EXPOSE 8090
CMD ["uvicorn", "runner:app", "--host", "0.0.0.0", "--port", "8090"]
