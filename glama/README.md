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

This directory is the source of truth for the Dockerfile pasted into Glama's
admin page. Keep that paste in sync with `glama/Dockerfile`. The image holds no
user data and no secrets and is not meant to be used as an Applire instance.
