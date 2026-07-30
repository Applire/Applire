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

"""
Static, render-time CV/cover-letter chrome labels keyed by output language (#4, ADR-038).

CV section headings and the cover-letter subject were hardcoded German (or, for
modern_swiss, bilingual; for tech_developer, English) regardless of the document's
output language — so an English CV showed German headings. These labels are resolved
from the target-job language at render time and injected into the Jinja context, so
every template's chrome follows the same language as its LLM-generated content.

Canonical set across all templates (per-template register intentionally normalised;
the language-follows-output invariant is the fix, exact wording can be refined later).
"""

_CV_LABELS: dict[str, dict[str, str]] = {
    "de": {
        "summary": "Profil",
        "experience": "Berufserfahrung",
        "education": "Ausbildung",
        "skills": "Kenntnisse",
        "languages": "Sprachen",
        "projects": "Projekte",
        "contact": "Kontakt",
        "present": "heute",
        "certifications": "Zertifizierungen",
        # #328: per-role quantified-fact furniture line (ADR-062 clause 1).
        "role_team_size": "Teamgröße",
        "role_budget": "Budget",
        "role_industry": "Branche",
    },
    "en": {
        "summary": "Profile",
        "experience": "Experience",
        "education": "Education",
        "skills": "Skills",
        "languages": "Languages",
        "projects": "Projects",
        "contact": "Contact",
        "present": "Present",
        "certifications": "Certifications",
        "role_team_size": "Team size",
        "role_budget": "Budget",
        "role_industry": "Industry",
    },
}

# `closing_punctuation` (#307): German takes NO comma after the Grußformel; English
# does after the complimentary close. Every letter template hardcoded a "," directly
# after `signature.closing`, so charter run #7 delivered "Mit freundlichen Grüßen,"
# to a German employer. Punctuation is chrome and follows the output language like
# every other value here — it is not part of the stored `closing` string.
_COVER_LETTER_LABELS: dict[str, dict[str, str]] = {
    "de": {"subject_prefix": "Bewerbung", "subject_at": "bei", "email": "E-Mail", "phone": "Telefon", "address": "Adresse", "closing": "Mit freundlichen Grüßen", "closing_punctuation": "", "salutation": "Sehr geehrte Damen und Herren,"},
    "en": {"subject_prefix": "Application", "subject_at": "at", "email": "Email", "phone": "Phone", "address": "Address", "closing": "Kind regards", "closing_punctuation": ",", "salutation": "Dear Sir or Madam,"},
}

# #261: the word framing a target bullet's paired measured outcome ("target X
# — measured: Y" / "Ziel X — gemessen: Y") — same language-follows-output
# invariant as the section headings above; this text is written INTO
# generated CV/letter content, not UI chrome, but the rule is identical.
_OUTCOME_FRAME_LABELS: dict[str, str] = {
    "de": "gemessen",
    "en": "measured",
}


def cv_labels(lang: str) -> dict[str, str]:
    """Return the CV section labels for `lang` (falls back to German — DACH default)."""
    return _CV_LABELS.get(lang, _CV_LABELS["de"])


def cover_letter_labels(lang: str) -> dict[str, str]:
    """Return the cover-letter chrome labels for `lang` (falls back to German)."""
    return _COVER_LETTER_LABELS.get(lang, _COVER_LETTER_LABELS["de"])


def outcome_frame_label(lang: str) -> str:
    """The "measured"/"gemessen" word for #261's target->outcome framing
    (falls back to German — DACH default, mirrors ``cv_labels``)."""
    return _OUTCOME_FRAME_LABELS.get(lang, _OUTCOME_FRAME_LABELS["de"])
