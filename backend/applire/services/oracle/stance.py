# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""US245 — target-vs-achieved stance classification (ADR-052 §2).

Marker classes are versioned DATA in ``oracle/markers/`` (community-extensible
per language, Norms-Engine pattern) — never code. Matching is word-boundary
aware on unicode-normalized text: NFKC, typographic apostrophes folded to
ASCII (the U+2019 lesson, 2026-07-11), casefolded.
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from applire.schemas.oracle import Stance

_MARKERS_DIR = Path(__file__).parent / "markers"

# Typographic apostrophe variants folded to ASCII before any marker matching.
_APOSTROPHES = ("’", "ʼ", "‘", "‛", "´", "`")


def normalize_stance_text(text: str) -> str:
    s = unicodedata.normalize("NFKC", text or "")
    for a in _APOSTROPHES:
        s = s.replace(a, "'")
    return re.sub(r"\s+", " ", s).casefold().strip()


def _boundary_pattern(markers: tuple[str, ...]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(m) for m in sorted(markers, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)")


@lru_cache(maxsize=1)
def _load_marker_patterns() -> tuple[re.Pattern[str], re.Pattern[str]]:
    """(aspirational, achieved) patterns from every stance_*.json marker file.

    Languages are merged: documents and vault evidence may mix DE and EN
    (ADR-038 reality), so classification never depends on language detection.
    """
    aspirational: list[str] = []
    achieved: list[str] = []
    for path in sorted(_MARKERS_DIR.glob("stance_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        aspirational += [normalize_stance_text(m) for m in data.get("aspirational", [])]
        achieved += [normalize_stance_text(m) for m in data.get("achieved", [])]
    return _boundary_pattern(tuple(aspirational)), _boundary_pattern(tuple(achieved))


@lru_cache(maxsize=1)
def marker_versions() -> dict[str, str]:
    """language -> marker-file version, for report/debug surfaces."""
    versions: dict[str, str] = {}
    for path in sorted(_MARKERS_DIR.glob("stance_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        versions[data.get("language", path.stem)] = data.get("version", "?")
    return versions


def classify_stance(text: str) -> Stance | None:
    """Classify a text's stance, or None when no marker class is present.

    When BOTH classes match, the EARLIEST marker governs: aspirational scopes
    ("aims to", "plans to", "soll") precede the infinitive they qualify, so
    "aims to cut onboarding time" reads aspirational even though "cut" alone
    is an achieved marker — while "Reduced costs and plans to cut more" still
    asserts a delivery and stays achieved.
    """
    aspirational_re, achieved_re = _load_marker_patterns()
    norm = normalize_stance_text(text)
    if not norm:
        return None
    asp = aspirational_re.search(norm)
    ach = achieved_re.search(norm)
    if asp and ach:
        return "aspirational" if asp.start() < ach.start() else "achieved"
    if ach:
        return "achieved"
    if asp:
        return "aspirational"
    return None
