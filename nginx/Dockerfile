# Applire reverse proxy (ADR-033/ADR-034) — the self-hosted entry point, shipped
# as a published image so `docker compose pull` fetches a complete, working stack
# with nothing for the operator to place on the host (ADR-032 amended 2026-07-03).
#
# The stock nginx image serves `/etc/nginx/conf.d/*.conf`; baking the config in as
# `default.conf` means it routes /api,/health,/static → backend:8000 and everything
# else → frontend:3000 out of the box. Operators who need a custom domain or TLS can
# still bind-mount their own file over /etc/nginx/conf.d/default.conf.
FROM nginx:alpine
COPY self-hosted.conf /etc/nginx/conf.d/default.conf
