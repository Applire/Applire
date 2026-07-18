# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""US249 (E044, ADR-054) — the typed public cover-letter content contract.

``letter_data`` was an untyped dict shaped only by the writer prompt; the
render_document agent door makes it a public, versioned contract. The contract
must validate LOSSLESSLY every shape the system produces today:
  - the writer prompt's schema (prompts/cover_letter.py SYSTEM_PROMPT)
  - MockLLMProvider's _COVER_LETTER_RESPONSE (ADR-047 mock-mirrors-shape)
  - legacy partial sections created by _backfill_sender_name ({"name": ...})
and must REJECT unknown fields on the render input (agent typos surface as
validation errors instead of silently dropped sections).
"""
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

_backend = Path(__file__).parent.parent.parent
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


# The writer-prompt shape (prompt mandates date/closing null — system chrome).
WRITER_SHAPE = {
    "header": {
        "name": "Anna Bauer",
        "address": "Hauptstraße 42, 10115 Berlin",
        "phone": "+49 170 1234567",
        "email": "anna.bauer@example.de",
        "photo_url": None,
    },
    "recipient": {
        "name": "Herr Dr. Müller",
        "title": "Personalleiter",
        "company": "TechVision GmbH",
        "address": "Unter den Linden 1, 10117 Berlin",
        "date": None,
    },
    "body": {"paragraphs": ["Sehr geehrter Herr Dr. Müller, …", "Hauptteil.", "Schluss."]},
    "signature": {"closing": None, "name": "Anna Bauer"},
}

# Post-generation persisted shape: date injected, closing normalized.
PERSISTED_SHAPE = {
    **WRITER_SHAPE,
    "recipient": {**WRITER_SHAPE["recipient"], "date": "18. Juli 2026"},
    "signature": {"closing": "Mit freundlichen Grüßen", "name": "Anna Bauer"},
}

# Legacy partial sections (_backfill_sender_name creates {"name": ...} only).
LEGACY_PARTIAL_SHAPE = {
    "header": {"name": "Anna Bauer"},
    "body": {"paragraphs": ["Text."]},
    "signature": {"name": "Anna Bauer"},
}


def test_mock_provider_shape_validates_losslessly():
    from applire.providers.llm.mock import _COVER_LETTER_RESPONSE
    from applire.schemas.cover_letter import LetterData

    model = LetterData.model_validate(_COVER_LETTER_RESPONSE)
    assert model.model_dump(mode="json") == _COVER_LETTER_RESPONSE


def test_writer_prompt_shape_validates_losslessly():
    from applire.schemas.cover_letter import LetterData

    model = LetterData.model_validate(WRITER_SHAPE)
    assert model.model_dump(mode="json") == WRITER_SHAPE


def test_persisted_shape_roundtrips():
    from applire.schemas.cover_letter import LetterData

    model = LetterData.model_validate(PERSISTED_SHAPE)
    dumped = model.model_dump(mode="json")
    assert dumped["recipient"]["date"] == "18. Juli 2026"
    assert dumped["signature"]["closing"] == "Mit freundlichen Grüßen"
    assert dumped["body"]["paragraphs"] == PERSISTED_SHAPE["body"]["paragraphs"]


def test_legacy_partial_sections_validate():
    """_backfill_sender_name writes {"name": ...}-only sections; recipient may be
    absent entirely on old rows. The contract must not require a data migration."""
    from applire.schemas.cover_letter import LetterData

    model = LetterData.model_validate(LEGACY_PARTIAL_SHAPE)
    assert model.header.name == "Anna Bauer"
    assert model.header.address == ""
    assert model.recipient.company is None
    assert model.signature.closing is None


def test_unknown_top_level_section_rejected():
    from applire.schemas.cover_letter import LetterData

    with pytest.raises(ValidationError) as exc_info:
        LetterData.model_validate({**WRITER_SHAPE, "subject": "Bewerbung als …"})
    # Subject is render-time-computed from job.role_title — never contract data.
    assert "subject" in str(exc_info.value)


def test_unknown_nested_field_rejected_with_field_path():
    from applire.schemas.cover_letter import LetterData

    bad = {**WRITER_SHAPE, "header": {**WRITER_SHAPE["header"], "linkedin": "x"}}
    with pytest.raises(ValidationError) as exc_info:
        LetterData.model_validate(bad)
    # The error must carry an agent-actionable location (US251 invalid_input).
    assert any(err["loc"][:2] == ("header", "linkedin") for err in exc_info.value.errors())


def test_paragraphs_required_nonempty():
    from applire.schemas.cover_letter import LetterData

    with pytest.raises(ValidationError):
        LetterData.model_validate({"body": {"paragraphs": []}, "header": {"name": "A"}})


def test_schema_version_constant():
    from applire.schemas.cover_letter import LETTER_SCHEMA_VERSION

    assert LETTER_SCHEMA_VERSION == "cover-letter/1"
