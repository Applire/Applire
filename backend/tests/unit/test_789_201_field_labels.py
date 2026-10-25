# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""789:201 — backend-written questions name a field in words, never a dotted
key; the backend table mirrors the frontend catalogs exactly."""

import json
from pathlib import Path

import pytest

from applire.services.profile.field_labels import FIELD_LABELS, SECTION_LABELS, field_label

_MESSAGES = Path(__file__).resolve().parents[3] / "frontend" / "messages"


@pytest.mark.parametrize(
    "section, field, lang, expected",
    [
        ("personal_info", "location", "de", "Ort (Persönliche Daten)"),
        ("languages", "level", "de", "Niveau (Sprachen)"),
        ("languages", "level", "en", "Level (Languages)"),
        ("work_experience", "budget_currency", "de", "budget currency (Berufserfahrung)"),
        ("unknown_section", "level", "fr", "Level"),
    ],
)
def test_field_label_is_words(section, field, lang, expected):
    label = field_label(section, field, lang)
    assert label == expected
    assert "." not in label and "_" not in label


@pytest.mark.parametrize("lang", ["de", "en"])
def test_the_mirror_matches_the_frontend_catalogs(lang):
    catalog = json.loads((_MESSAGES / f"{lang}.json").read_text(encoding="utf-8"))
    for key, value in catalog["health"]["fieldLabel"].items():
        if key.startswith("summary"):
            continue
        assert FIELD_LABELS[key][lang] == value, key
    for key, value in catalog["review"]["section"].items():
        if key == "*":
            continue
        assert SECTION_LABELS[key][lang] == value, key
