#!/bin/sh
# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# ADR-091 cl. 13 as amended by MD-33: nginx trusts NO peer to supply
# X-Forwarded-For unless the operator names their TLS proxy with one line in
# .env:  APPLIRE_TRUSTED_PROXY=<ip or cidr>[,<ip or cidr>…]
# This runs from the stock image's /docker-entrypoint.d/ before nginx starts and
# writes the trust directives for the config's `include /etc/nginx/applire/*.conf`.
# Empty/unset -> an empty file (trust nobody). An invalid entry stops the
# container (exit 1): silently trusting nobody would hide a typo, and anything
# but an address must never reach the nginx config.
# A range wider than /16 (IPv4) or /48 (IPv6) is refused the same way (founder
# ruling 2026-10-10, JF-O-K2.5 / SF-IAM.6): `0.0.0.0/0` would let every client
# choose its own throttle identity. Docker's /16 bridge stays allowed.
# $1 = output directory (tests only; the image runs this without arguments).
set -eu

out_dir="${1:-/etc/nginx/applire}"
out="$out_dir/trusted-proxy.conf"
mkdir -p "$out_dir"
: > "$out"

value="${APPLIRE_TRUSTED_PROXY:-}"
[ -z "$(printf '%s' "$value" | tr -d ' ')" ] && exit 0

ipv4='^([0-9]{1,3}\.){3}[0-9]{1,3}(/([0-9]|[12][0-9]|3[0-2]))?$'
ipv6='^[0-9A-Fa-f:.]*:[0-9A-Fa-f:.]*(/([0-9]|[1-9][0-9]|1[01][0-9]|12[0-8]))?$'

for entry in $(printf '%s' "$value" | tr ',' ' '); do
    if printf '%s' "$entry" | grep -Eq "$ipv4"; then
        for octet in $(printf '%s' "${entry%%/*}" | tr '.' ' '); do
            if [ "$octet" -gt 255 ]; then
                echo "applire: APPLIRE_TRUSTED_PROXY entry '$entry' is not an IP address or CIDR range" >&2
                exit 1
            fi
        done
        min_prefix=16
    elif printf '%s' "$entry" | grep -Eq "$ipv6"; then
        min_prefix=48
    else
        echo "applire: APPLIRE_TRUSTED_PROXY entry '$entry' is not an IP address or CIDR range" >&2
        exit 1
    fi
    case "$entry" in
        */*)
            prefix="${entry##*/}"
            if [ "$prefix" -lt "$min_prefix" ]; then
                echo "applire: APPLIRE_TRUSTED_PROXY entry '$entry' is wider than /$min_prefix — name your proxy's address or a range of at most /$min_prefix (every address in a wider range could pick its own sign-in throttle identity)" >&2
                exit 1
            fi
            ;;
    esac
    printf 'set_real_ip_from %s;\n' "$entry" >> "$out"
done
echo "applire: trusting X-Forwarded-For from: $value"
