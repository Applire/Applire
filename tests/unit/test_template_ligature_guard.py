# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""786:L23 (Hazelnut build 2) — a template-source assertion for the ligature guard.

Calibri resolves to Carlito in the render image, whose ``ti``/``fb`` ligature
glyphs carry no Unicode mapping: ``pypdf`` extracts NUL bytes and words such as
"Aufbau" / "Prozessoptimierung" become unfindable for an ATS (measured 2026-08-31
on the real ``_html_to_pdf`` path: 10 NULs with default ligatures, 0 with the
property). ``compact_pro_letter`` is safe ONLY because the same CSS rule sets
``font-variant-ligatures: none`` — nothing tested that. The ADR-039 round-trip
catches a deletion only when its fixture prose happens to contain an affected pair,
so the guard is pinned at the source, like the #621 margin gate.

The rule: every CSS rule in a shipped template whose ``font-family`` names a font
with unmapped ligature glyphs (Calibri / Carlito) declares
``font-variant-ligatures: none`` in that same rule.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[2] / "backend" / "applire" / "templates"
#: Fonts whose ligature glyphs extract as NUL (measured; extend when another is found).
UNMAPPED_LIGATURE_FONTS = ("calibri", "carlito")

_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def _rules(source: str) -> list[tuple[str, str]]:
    css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", source, re.S | re.I))
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [(sel.strip(), body) for sel, body in _RULE_RE.findall(css)]


def _ligature_font_rules() -> list[tuple[str, str, str]]:
    out = []
    for path in sorted(TEMPLATES.glob("*.html.j2")):
        for selector, body in _rules(path.read_text(encoding="utf-8")):
            m = re.search(r"font-family\s*:\s*([^;]+)", body, re.I)
            if m and any(f in m.group(1).lower() for f in UNMAPPED_LIGATURE_FONTS):
                out.append((path.name, selector, body))
    return out


def test_the_guard_is_not_vacuous():
    """compact_pro_letter's Calibri body rule is the measured case — it must be found."""
    names = {name for name, _sel, _body in _ligature_font_rules()}
    assert "compact_pro_letter.html.j2" in names


@pytest.mark.parametrize("name, selector, body", _ligature_font_rules())
def test_a_ligature_hazard_font_rule_disables_ligatures(name, selector, body):
    assert re.search(r"font-variant-ligatures\s*:\s*none\b", body, re.I), (
        f"{name} `{selector}` uses a font whose ligature glyphs extract as NUL "
        "(Calibri -> Carlito) without `font-variant-ligatures: none` in the same rule — "
        "an invisible ATS regression (786:L23, SF-EXPORT.3)."
    )
