# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#447 / ADR-084 clause 6 — the injection corpus's own shape, guarded hermetically.

The corpus is exercised on a real provider by
``tests/integration/test_injection_corpus_llm.py``, which is fixture-gated and
therefore a pin rather than a gate. This file is the part that CAN run in the
unit tier: it asserts the corpus still HAS the shape the measurement depends on.
Without it, a corpus that quietly lost half its cases — or a hostile case with
nothing detectable in it — would stay invisible until the next manual run, and
the manual run would then report a comfortable pass over a corpus measuring
nothing. That is the ``fixture-gated tests are pins, not gates`` failure applied
to the fixture itself.
"""
import json
from pathlib import Path

CORPUS_DIR = Path(__file__).parent.parent / "files" / "injection_corpus"
MANIFEST = json.loads((CORPUS_DIR / "manifest.json").read_text(encoding="utf-8"))
CASES = MANIFEST["cases"]
HOSTILE = [c for c in CASES if c["kind"] == "hostile"]
BENIGN = [c for c in CASES if c["kind"] == "benign"]


def test_at_least_twelve_hostile_cases():
    assert len(HOSTILE) >= 12, f"only {len(HOSTILE)} hostile cases"


def test_a_benign_twin_exists():
    """A corpus with no control measures nothing: 'no attack succeeded' over
    postings the model was too timid to read is not a pass."""
    assert BENIGN


def test_every_attack_class_is_covered_in_both_languages():
    by_class: dict[str, set[str]] = {}
    for c in HOSTILE:
        by_class.setdefault(c["class"], set()).add(c["lang"])
    for cls, langs in sorted(by_class.items()):
        assert langs == {"de", "en"}, f"{cls} is only covered in {sorted(langs)}"
    assert len(by_class) >= 5, f"only {len(by_class)} attack classes: {sorted(by_class)}"


def test_every_case_has_its_posting_file():
    for c in CASES:
        assert (CORPUS_DIR / f"{c['id']}.txt").exists(), c["id"]


def test_every_hostile_case_has_something_detectable():
    for c in HOSTILE:
        assert c["forbidden"], f"{c['id']}: hostile case with no marker to detect"
        text = (CORPUS_DIR / f"{c['id']}.txt").read_text(encoding="utf-8")
        for marker in c["forbidden"]:
            assert marker in text, f"{c['id']}: marker {marker!r} is not in the posting"


def test_every_hostile_case_names_the_benign_base_it_is_measured_against():
    ids = {c["id"] for c in CASES}
    for c in HOSTILE:
        assert c["base"] in ids, f"{c['id']}: base {c['base']!r} is not in the corpus"


def test_the_manifest_states_the_scope_it_does_not_cover():
    """Hostile CV/import text is a different trust class (SEC-13 [MU-gate]).
    The manifest must say so, so the gap is a recorded decision rather than an
    omission a later reader mistakes for coverage."""
    assert "SEC-13" in MANIFEST["scope"]
