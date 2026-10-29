# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""U-13 (Hazelnut build 5, delivery run 5): an acronym and its fixed expansion
name ONE skill.

Rep a's Oracle graded the chip "Continuous integration and delivery" unbacked.
Rep b's Oracle graded the same chip grounded, from the same vault line ("CI/CD with
GitHub Actions …"). The only bridge was the keyword ledger's sibling surface forms,
and the model chooses those. Rep b's row carried "CI/CD" and rep a's carried only
"CI/CD pipelines". So one fact got two verdicts. The person then added the skill
"CI/CD (GitHub Actions)" in their own words, and "Jetzt prüfen" still said unbacked.

This module is the deterministic bridge: a CLOSED table of acronym ↔ expansion
classes, read symmetrically (any member stands for every other). The same closed-
list precedent covers the fixed degree abbreviations in ``credentials.py``. No
initialism is derived from letters, because "SAP" is not "supply assurance plan".
ADR-062 cl. 1: a fixed table lookup is a fact, not an equivalence judgement.
"""
from __future__ import annotations

import re

#: Each tuple is ONE name. Lower-case, single-spaced. Add a class only when every
#: member is unambiguous in a CV (no "CSV": comma-separated values vs. computer
#: system validation; no bare "AI"/"ML" two-letter tokens inside other words).
EQUIVALENCE_CLASSES: tuple[tuple[str, ...], ...] = (
    (
        "ci/cd",
        "continuous integration and delivery",
        "continuous integration and deployment",
        "continuous integration and continuous delivery",
        "continuous integration and continuous deployment",
        "continuous integration/continuous delivery",
        "continuous integration/continuous deployment",
        "continuous integration / continuous delivery",
        "continuous integration / continuous deployment",
    ),
    ("model context protocol", "mcp"),
    ("large language models", "llms"),
    ("large language model", "llm"),
    ("retrieval-augmented generation", "retrieval augmented generation", "rag"),
    ("natural language processing", "nlp"),
)


def _pattern(form: str) -> re.Pattern[str]:
    return re.compile(r"(?<![\w/])" + re.escape(form).replace(r"\ ", r"\s+") + r"(?![\w/])", re.IGNORECASE)


_COMPILED: tuple[tuple[tuple[str, re.Pattern[str]], ...], ...] = tuple(
    tuple((form, _pattern(form)) for form in cls) for cls in EQUIVALENCE_CLASSES
)


def acronym_equivalents(name: str) -> list[str]:
    """The other spellings of ``name``: every whole-phrase occurrence of a class
    member is replaced by each other member of its class ("CI/CD (GitHub Actions)"
    → "continuous integration and delivery (GitHub Actions)", …; "Continuous
    integration and delivery" → "ci/cd", …). Empty when no member occurs. The
    original ``name`` is never in the result."""
    if not isinstance(name, str) or not name.strip():
        return []
    out: list[str] = []
    seen = {" ".join(name.lower().split())}
    for cls in _COMPILED:
        for form, pat in cls:
            if not pat.search(name):
                continue
            for other, _ in cls:
                if other == form:
                    continue
                alt = pat.sub(other, name)
                key = " ".join(alt.lower().split())
                if key not in seen:
                    seen.add(key)
                    out.append(alt)
    return out
