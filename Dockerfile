# syntax=docker/dockerfile:1

FROM node:20-trixie-slim AS opencode

RUN npm install -g opencode-ai@1.18.26

FROM python:3.14-slim-trixie

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

COPY --from=opencode \
  /usr/local/lib/node_modules/opencode-ai/bin/opencode.exe \
  /usr/local/bin/opencode
RUN opencode --version

WORKDIR /app

COPY pyproject.toml README.md ./
COPY labpapers ./labpapers
COPY labrepos ./labrepos
COPY university ./university

RUN python -m pip install --no-cache-dir . \
    && mkdir -p /data /reports /root/.local/share/opencode

# This image is deployed by a rootless Docker daemon. Container UID 0 maps to
# the unprivileged host account and can therefore read its mode-0600 OpenCode
# and Cloudflare state and write its host-owned bind mounts.
ENV HOME=/root

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --retries=6 --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4).read(1)"

CMD ["gymnasium", "--host", "0.0.0.0", "--port", "8000", "--db", "/data/gymnasium.db", "--reports", "/reports", "--docs-dir", "/data/documents", "--ingest-on-start"]
