# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""785:L131 / ADR-095 cl. 7 — every published image gets a `:{{major}}` tag
(`:1` = "the newest 1.x") under exactly the predicate that moves `:latest`, so a
pre-release never moves it and an operator pinning `:1` never gets a 2.0."""

from __future__ import annotations

import re
from pathlib import Path

_WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"
_STABLE = "enable=${{ github.event_name == 'release' && github.event.release.prerelease == false }}"


def _tag_blocks() -> list[list[str]]:
    """The `tags:` lists of every docker/metadata-action step."""
    blocks, current = [], None
    for line in _WORKFLOW.read_text(encoding="utf-8").splitlines():
        if re.match(r"^\s+tags: \|\s*$", line):
            current = []
            blocks.append(current)
        elif current is not None:
            if line.strip().startswith("type="):
                current.append(line.strip())
            else:
                current = None
    return [b for b in blocks if b]


def test_three_images_carry_semver_tags():
    blocks = [b for b in _tag_blocks() if any("type=semver" in t for t in b)]
    assert len(blocks) == 3, blocks


def test_every_image_gets_a_major_tag_under_the_latest_predicate():
    for block in (b for b in _tag_blocks() if any("type=semver" in t for t in b)):
        latest = [t for t in block if t.startswith("type=raw,value=latest")]
        major = [t for t in block if t.startswith("type=semver,pattern={{major}},")]
        assert len(latest) == 1 and len(major) == 1, block
        assert latest[0].endswith(_STABLE) and major[0].endswith(_STABLE), block
