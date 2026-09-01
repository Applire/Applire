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

"""E057/US297 — the `.docx` export must render every letter the PDF renders.

**Found on the real path, not in a test.** Downloading a genuine generated
letter from the dev stack through the proxy returned **409 Conflict** while
`/pdf` returned 200 for the same record. The stored `letter_data` carried a
stray `body.signature` — a duplicate, half-empty copy of the signature that
already exists correctly at top level:

    "body": {"signature": {"name": "STEFAN BRANDT", "closing": null},
             "paragraphs": [...]}

`LetterBody` is `extra="forbid"`, so strict validation rejects it. The PDF path
never noticed because it renders the raw dict through Jinja, and a template
reads only the fields it names.

The writer prompt asks for `signature` at the **top level** and a `body`
containing only `paragraphs`, so this is a model deviation — and nothing
validates `letter_data` before persisting it, which is why it survived. Fixing
the producer is a separate change with its own verification; what must be true
here is narrower and non-negotiable: **the export renders exactly what the PDF
renders, and a stray key the PDF ignores must not turn into a failed download.**

Every stored letter checked in the dev database (n=1) failed this validation,
so the export was broken for the only real letter available — a small sample,
but not a hypothetical one.
"""
import sys
import uuid
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

# Verbatim shape of the real record that produced the 409 (id 0c265587…),
# trimmed to the parts that matter.
REAL_LETTER_WITH_STRAY_KEY = {
    "body": {
        "signature": {"name": "STEFAN BRANDT", "closing": None},   # <- the stray
        "paragraphs": [
            "Sehr geehrte Damen und Herren,",
            "mit 14 Jahren Erfahrung in der Produktionsleitung bewerbe ich mich.",
        ],
    },
    "header": {
        "name": "STEFAN BRANDT", "email": "stefan.brandt@example.com",
        "phone": "+49 261 0000000", "address": "Koblenz", "photo_url": None,
    },
    "recipient": {
        "date": "31. August 2026", "name": None, "title": None,
        "address": None, "company": "Rheinwerk Verpackungen GmbH",
    },
    "signature": {"name": "STEFAN BRANDT", "closing": "Mit freundlichen Grüßen"},
}


def test_a_stray_nested_key_does_not_break_the_export():
    """The regression: this exact dict raised
    `1 validation error for LetterData / body.signature / Extra inputs are
    not permitted` and the endpoint answered 409."""
    from applire.services.cover_letter import _coerce_stored_letter_data

    letter = _coerce_stored_letter_data(REAL_LETTER_WITH_STRAY_KEY)

    assert letter.signature.closing == "Mit freundlichen Grüßen"
    assert letter.signature.name == "STEFAN BRANDT"
    assert len(letter.body.paragraphs) == 2


def test_the_export_renders_the_real_record_end_to_end():
    from applire.services.cover_letter import _coerce_stored_letter_data
    from applire.services.office_export.extract import extract_docx_text
    from applire.services.office_export.letter_docx import render_letter_docx

    letter = _coerce_stored_letter_data(REAL_LETTER_WITH_STRAY_KEY)
    text = extract_docx_text(
        render_letter_docx(letter, lang="de", accent_color="#1a3a5c")
    )

    assert "Sehr geehrte Damen und Herren," in text
    assert "Rheinwerk Verpackungen GmbH" in text
    assert "Mit freundlichen Grüßen" in text


def test_the_top_level_signature_wins_over_the_stray_duplicate():
    """The stray copy has `closing: null`. Silently preferring it would blank
    the sign-off — dropping the extra must never change rendered content."""
    from applire.services.cover_letter import _coerce_stored_letter_data

    letter = _coerce_stored_letter_data(REAL_LETTER_WITH_STRAY_KEY)

    assert letter.signature.closing is not None


def test_a_genuinely_malformed_letter_still_raises():
    """Tolerance is scoped to UNKNOWN keys. A missing required field, or a
    wrong type on a known one, must still fail loudly — this must not become
    a blanket 'accept anything' that hides real corruption."""
    from applire.services.cover_letter import _coerce_stored_letter_data

    with pytest.raises(Exception):
        _coerce_stored_letter_data({"header": {}, "recipient": {}})   # no body

    with pytest.raises(Exception):
        _coerce_stored_letter_data(
            {"body": {"paragraphs": "not a list"}}
        )
