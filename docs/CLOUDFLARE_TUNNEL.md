# Hosting Gymnasium with Docker and Cloudflare Tunnel

Gymnasium runs as a two-container Compose application:

- `app` contains Python, the Gymnasium package, document conversion
  dependencies, and the OpenCode CLI.
- `cloudflared` owns the named tunnel and routes the public hostname to the
  app over the private Compose network.

All mutable state remains on the host. Images contain no database, documents,
reports, account credentials, or Cloudflare credentials.

| State | Default host path | Container path |
| --- | --- | --- |
| SQLite, documents, caches | `./data` | `/data` |
| Ingest reports | `./reports` | `/reports` |
| OpenCode auth and state | `~/.gymnasium-opencode` | `/root/.local/share/opencode` |
| Cloudflare cert and tunnel JSON | `~/.cloudflared` | `/etc/cloudflared` |

## Security

The application stores its own usernames and passwords in plaintext. Use a
strong, unique password and consider putting Cloudflare Access in front of the
site. The Compose app port is published only on `127.0.0.1`; public traffic
reaches it through the tunnel sidecar.

## Host prerequisites

The host needs a rootless Docker Engine with the Compose plugin. The rootless
daemon runs as the deployment account, uses
`/run/user/$UID/docker.sock`, and should be enabled as a systemd user service
with login lingering enabled. Select its context before starting Gymnasium:

```bash
docker context use rootless
docker info --format '{{json .SecurityOptions}}'
```

The output must include `name=rootless`; `start.sh` refuses to deploy through a
rootful daemon. Neither Python, OpenCode, nor cloudflared is required on the
host.

The processes run as UID 0 *inside* their containers so that they map to the
unprivileged deployment account outside the rootless user namespace. They do
not have host root privileges. This mapping preserves access to the account's
host-owned bind mounts, including mode-0600 credentials.

The Cloudflare directory must contain `cert.pem` and the credential JSON for
the configured named tunnel. To preserve an existing URL during a machine
migration, copy those files to the same host directory and keep the existing
`TUNNEL_NAME` and `APP_HOST` values.

OpenCode credentials can be initialized on the host before startup:

```bash
mkdir -p ~/.gymnasium-opencode
cp ~/.local/share/opencode/auth.json ~/.gymnasium-opencode/auth.json
chmod 600 ~/.gymnasium-opencode/auth.json
```

Alternatively, authenticate through a one-off container:

```bash
docker compose run --rm app opencode auth login
```

## Configure and start

```bash
cp .env.example .env
./start.sh --detach
```

When `TUNNEL_NAME` and `APP_HOST` are both set, `start.sh` enables the tunnel
profile. It builds both images, starts them, and waits for the application and
the public URL to become healthy. `restart: unless-stopped` brings the
containers back after Docker or the host restarts.

Useful commands:

```bash
docker compose --profile tunnel ps
docker compose --profile tunnel logs -f
docker compose --profile tunnel down
```

## Local-only mode

Comment out both `TUNNEL_NAME` and `APP_HOST`, then run `./start.sh`. Only the
app container starts, at `http://127.0.0.1:$PORT`.

## Repair a stale tunnel

The normal startup reuses the existing named tunnel and idempotently maintains
its DNS route. If the tunnel itself must be replaced, run:

```bash
./reset-tunnel.sh
./start.sh --detach
```

Resetting deletes and recreates the named tunnel, writes its new credential
JSON into the host-mounted Cloudflare directory, and points the existing
hostname at the new tunnel.
