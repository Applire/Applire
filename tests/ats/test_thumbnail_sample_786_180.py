# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""786:180 (Hazelnut build 8): the template-picker thumbnails render shipped sample
data. It carried a real employer ("Novartis AG", earlier "Roche GmbH") and a real
university, and English bullets in the German sample ("Led backend architecture
redesign"). The whole sample source is walked here, not only the cited strings."""
import re
from pathlib import Path

from applire.services.thumbnails import _SAMPLE_DATA

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "generate_thumbnails.py"

#: Real organisations seen in this sample's history or near it in the codebase.
_REAL = ("novartis", "roche", "tu münchen", "lmu münchen", "varena", "siemens", "bosch", "sap ")
#: Common English function words / verbs; none belongs in German sample prose.
_ENGLISH = {"the", "with", "and", "of", "for", "led", "developed", "maintained", "mentored",
            "junior", "engineers", "redesign", "databases", "built"}


def _strings(obj) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _strings(v)]
    return []


def test_the_sample_names_no_real_organisation():
    text = " ".join(_strings(_SAMPLE_DATA.model_dump())).casefold()
    for name in _REAL:
        assert name not in text, f"real organisation {name!r} in the template sample"
    script = _SCRIPT.read_text(encoding="utf-8").casefold()
    for name in _REAL:
        assert name not in script, f"real organisation {name!r} in scripts/generate_thumbnails.py"


def test_the_german_sample_prose_is_german():
    data = _SAMPLE_DATA.model_dump()
    prose = [data["summary"]] + [
        b for w in data["work_history"] for b in w["bullets"] + [x for p in w["projects"] for x in p["bullets"]]
    ]
    assert len(prose) >= 5
    for line in prose:
        words = {w.casefold() for w in re.findall(r"[A-Za-zÄÖÜäöüß]+", line)}
        assert not words & _ENGLISH, f"English words {sorted(words & _ENGLISH)} in German sample: {line!r}"
