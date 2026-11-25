#!/usr/bin/env python3
# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""US371 — stamp ``server.json`` with a release's version before it is published.

The MCP Registry entry (``server.json``) names the backend image by tag, and the
tag is part of the OCI ``identifier`` (the registry refuses a separate
``version`` on an OCI package). A release tag ``vX.Y.Z`` publishes the image as
``ghcr.io/applire/applire-backend:X.Y.Z`` (release.yml, ``type=semver,
pattern={{version}}``), so both ``version`` and the identifier's tag become
``X.Y.Z`` — the entry always points at the image of the release it describes.

Run by ``release.yml`` in its own checkout of the tag, before
``mcp-publisher validate`` / ``publish``; the committed file is not rewritten in
the repository. ``python scripts/server_json_release.py [--require-stable] v1.0.0 [server.json]``

``--require-stable`` (the publish job uses it) states the publish rule
positively: the version must be ``MAJOR.MINOR.PATCH`` with ``MAJOR >= 1`` and no
pre-release suffix (ADR-095 am. 2026-11-13, second: the registry publish waits
for 1.0.0). The job's ``if:`` already skips the usual non-stable shapes; this
check fails the job closed on any shape the expression language cannot rule
out (e.g. ``v00.1.0``), before anything is published.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_TAG = re.compile(r"^v(?P<version>\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)$")


def version_from_tag(tag: str) -> str:
    match = _TAG.match(tag.strip())
    if match is None:
        raise ValueError(f"not a release tag of the form vX.Y.Z[-pre]: {tag!r}")
    return match.group("version")


_STABLE = re.compile(r"^[1-9][0-9]*\.[0-9]+\.[0-9]+$")


def require_stable(version: str) -> str:
    """The registry-publish rule, stated positively: 1.0.0 or later, no suffix."""
    if _STABLE.match(version) is None:
        raise ValueError(
            f"not a stable release >= 1.0.0: {version!r} "
            "(the MCP Registry publish waits for 1.0.0, ADR-095)"
        )
    return version


def stamp(server: dict, tag: str) -> dict:
    """Return a copy of ``server`` whose version and OCI tag are the release's."""
    version = version_from_tag(tag)
    out = json.loads(json.dumps(server))
    out["version"] = version
    oci = [p for p in out.get("packages", []) if p.get("registryType") == "oci"]
    if len(oci) != 1:
        raise ValueError(f"expected exactly one OCI package, found {len(oci)}")
    image = oci[0]["identifier"].rsplit(":", 1)[0]
    oci[0]["identifier"] = f"{image}:{version}"
    return out


def main(argv: list[str]) -> int:
    args = list(argv[1:])
    stable_only = "--require-stable" in args
    args = [a for a in args if a != "--require-stable"]
    if len(args) not in (1, 2):
        print(__doc__, file=sys.stderr)
        return 2
    path = Path(args[1] if len(args) == 2 else "server.json")
    if stable_only:
        try:
            require_stable(version_from_tag(args[0]))
        except ValueError as exc:
            print(f"refusing to stamp for publishing: {exc}", file=sys.stderr)
            return 1
    stamped = stamp(json.loads(path.read_text(encoding="utf-8")), args[0])
    path.write_text(json.dumps(stamped, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{path}: version {stamped['version']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
