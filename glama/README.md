# Glama introspection image

Glama builds a Dockerfile, starts the server over stdio and asks it for its tool
list (`initialize`, `tools/list`). Applire's MCP server (`python -m applire.mcp`)
refuses to start without a valid agent token checked against PostgreSQL (S-5),
and there is deliberately no token-less mode. This image is therefore
self-contained: it runs a throwaway PostgreSQL inside the container, applies the
migrations, claims the empty instance with the normal `create-admin` code path,
mints an `agent` token and starts the real server with it.

- `Dockerfile` - `FROM ghcr.io/applire/applire-backend:latest` (override with
  `--build-arg APPLIRE_IMAGE=...`), adds PostgreSQL 16 and pgvector.
- `entrypoint.sh` - initdb, migrate, seed, `exec python -m applire.mcp`. All
  setup output goes to stderr; stdout carries only the server's JSON-RPC.
- `seed.py` - claims the instance and prints the raw token (to the entrypoint
  only; random e-mail and password, nothing fixed, nothing in the image layers).

Build and run locally (build context = repository root):

    docker build -f glama/Dockerfile -t applire-glama .
    docker run -i --rm applire-glama

A cold start takes about 20-25 s (initdb plus the migrations).

## Glama's admin form

Glama's admin page does not take a Dockerfile. It generates one from a form:
a Debian base image, a Python version, build steps and CMD arguments, with the
repository cloned as the working directory. `entrypoint.sh` finds the backend
and `seed.py` relative to its own location, so the same script serves both
layouts. The form values, tested on 2026-11-18 against a debian:trixie-slim
image with uv-installed Python and the repository at /app:

- Base image: `debian:trixie-slim` · Python version: `3.12`
- Build steps:

      ["apt-get update && apt-get install -y --no-install-recommends postgresql postgresql-17-pgvector && rm -rf /var/lib/apt/lists/*", "uv venv /opt/applire --python 3.12", "uv pip install --no-cache --python /opt/applire/bin/python -r backend/requirements.txt", "uv pip install --no-cache --python /opt/applire/bin/python -e backend"]

- CMD arguments:

      ["env", "PATH=/opt/applire/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "bash", "glama/entrypoint.sh"]

- Placeholder parameters: `{"APPLIRE_AGENT_TOKEN": "apl_placeholder"}`. The
  form requires a value for the schema's required variable. The entrypoint
  replaces it with the token it mints, so the placeholder never reaches the
  server.

Keep these values in sync with this directory. The image holds no
user data and no secrets and is not meant to be used as an Applire instance.
