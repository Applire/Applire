# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Self-contained image for Glama's MCP introspection (initialize -> tools/list).
# Applire's MCP server refuses to start without a valid agent token checked
# against PostgreSQL (S-5), so this image adds a throwaway PostgreSQL and seeds
# it at start. Build context = repository root:
#   docker build -f glama/Dockerfile .
ARG APPLIRE_IMAGE=ghcr.io/applire/applire-backend:latest
FROM ${APPLIRE_IMAGE}

# PostgreSQL 16 (Ubuntu noble) + pgvector: the first migration runs CREATE EXTENSION vector.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql postgresql-16-pgvector \
    && rm -rf /var/lib/apt/lists/*

COPY glama/entrypoint.sh /app/glama_entrypoint.sh
COPY glama/seed.py /app/glama_seed.py
RUN chmod +x /app/glama_entrypoint.sh

ENTRYPOINT ["/app/glama_entrypoint.sh"]
