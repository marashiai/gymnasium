# syntax=docker/dockerfile:1

FROM node:20-trixie-slim AS opencode

RUN npm install -g opencode-ai@1.18.26

FROM python:3.14-slim-trixie

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    GYM_EMBED_CACHE=/opt/gymnasium/models

COPY --from=opencode \
  /usr/local/lib/node_modules/opencode-ai/bin/opencode.exe \
  /usr/local/bin/opencode
RUN opencode --version

WORKDIR /app

RUN python -m pip install --no-cache-dir \
      fastembed==0.8.0 sqlite-vec==0.1.9 mcp==2.1.1 \
    && mkdir -p "$GYM_EMBED_CACHE" \
    && python -c "from fastembed import TextEmbedding; m=TextEmbedding(model_name='BAAI/bge-small-en-v1.5', cache_dir='$GYM_EMBED_CACHE'); next(iter(m.embed(['Gymnasium model warmup'])))"

COPY pyproject.toml README.md opencode.json ./
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

HEALTHCHECK --interval=10s --timeout=5s --retries=12 --start-period=20s \
  CMD python -c "import socket,urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4).read(1); socket.create_connection(('127.0.0.1',8010),4).close()"

CMD ["gymnasium", "--host", "0.0.0.0", "--port", "8000", "--mcp-host", "0.0.0.0", "--mcp-port", "8010", "--db", "/data/gymnasium.db", "--reports", "/reports", "--docs-dir", "/data/documents", "--ingest-on-start"]
