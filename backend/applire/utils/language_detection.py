# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""Deterministic DE/EN document-language detection (ADR-038).

Documents (tailored CV, cover letter) must follow the language the job
description is *written in* — not ``language_requirement``, which describes
what the job demands of the candidate (e.g. "Bilingual DE/EN") and is
therefore the wrong routing signal.

Stopword-frequency scoring keeps this dependency-free and deterministic.
Scope is DE/EN only, matching the two supported locales in ADR-038.
"""

import re

# High-frequency function words that are unambiguous between the two languages.
# Words shared by both (e.g. "in", "an", "war") are deliberately excluded.
_GERMAN_STOPWORDS = frozenset(
    "der die das und oder für mit von sind wir sie eine ein einen einem nicht "
    "als auf im zu bei werden aus dem den des über nach durch ihre unsere "
    "sowie bzw kenntnisse erfahrung aufgaben wir bieten suchen".split()
)
_ENGLISH_STOPWORDS = frozenset(
    "the and of to for with you we are is our your will on at as be this "
    "that from or have has years experience skills team work looking role "
    "responsibilities requirements".split()
)

_WORD_RE = re.compile(r"[a-zäöüß]+")
_UMLAUT_RE = re.compile(r"[äöüß]")


def detect_language(text: str) -> str:
    """Classify *text* as 'de' or 'en'. Ties and empty input default to 'de'
    (DACH-first platform; matches the previous fallback)."""
    lowered = text.lower()
    words = _WORD_RE.findall(lowered)
    de_score = sum(1 for w in words if w in _GERMAN_STOPWORDS)
    en_score = sum(1 for w in words if w in _ENGLISH_STOPWORDS)
    # Umlauts/ß are a strong German signal independent of stopword overlap.
    de_score += 2 * len(_UMLAUT_RE.findall(lowered))
    return "en" if en_score > de_score else "de"


def resolve_jd_language(job) -> str:
    """Return the JD document language for a JobAnalysis row.

    Prefers the ``jd_language`` column (set at analysis time); falls back to
    detection on ``raw_text`` for rows that pre-date migration 0032.

    This is the DETECTION primitive — the language the JD is *written in*.
    A user's language choice cannot change that; the E054 override sits in
    :func:`resolve_document_language` ABOVE this function. Document surfaces
    must call the seam, not this primitive (ADR-038 amendment 2026-08-23,
    clause 2); direct callers are the seam itself, the conversation fallback
    (``get_conversation_language``) and JD-analysis stamping.
    """
    return job.jd_language or detect_language(job.raw_text or "")


def resolve_document_language(application, job) -> str:
    """Return the language for an employer-facing document of *application*.

    ADR-038 amendment 2026-08-23 (E054): ``applications.language_override``
    beats detection; NULL (or no application row) falls back to
    :func:`resolve_jd_language`. Callers in a generation run must resolve
    ONCE and thread the value (clause 3a — the override is user-mutable
    while a background generation is in flight); read/render/edit paths use
    the document's own pinned ``document_language`` instead (clause 3b).
    """
    if application is not None and getattr(application, "language_override", None):
        return application.language_override
    return resolve_jd_language(job)
