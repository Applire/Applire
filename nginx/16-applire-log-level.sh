#!/bin/sh
# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# 785:L71 (founder ruling E12-785:L71, 2026-10-22): on a 502/504 nginx's error log
# prints the whole request line, query string included — a 60-minute signed
# download link (`?sig=`) or a single-use sign-in code (`?code=`). Those lines
# follow the operator's existing debug switch instead of always being written:
# LOG_LEVEL=DEBUG -> `error` (full upstream error lines, for diagnosing a 502);
# anything else -> `crit` (no request lines). Same mechanism as
# 15-applire-trusted-proxy.sh: runs from the stock image's /docker-entrypoint.d/
# and writes a file the config includes from /etc/nginx/applire/.
# $1 = output directory (tests only; the image runs this without arguments).
set -eu

out_dir="${1:-/etc/nginx/applire}"
out="$out_dir/error-log.conf"
mkdir -p "$out_dir"

level="$(printf '%s' "${LOG_LEVEL:-INFO}" | tr -d ' ' | tr '[:lower:]' '[:upper:]')"
if [ "$level" = "DEBUG" ]; then
    printf 'error_log /var/log/nginx/error.log error;\n' > "$out"
else
    printf 'error_log /var/log/nginx/error.log crit;\n' > "$out"
fi
