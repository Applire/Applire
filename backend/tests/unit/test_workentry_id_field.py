# Copyright (C) 2024-2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the auto-generated id on WorkEntry."""
import uuid

from applire.schemas.profile import WorkEntry


def test_new_workentry_gets_id():
    we = WorkEntry(company="Roche", role="Engineer")
    assert isinstance(we.id, str)
    # round-trips as UUID
    uuid.UUID(we.id)


def test_explicit_id_is_preserved():
    fixed = "11111111-1111-1111-1111-111111111111"
    we = WorkEntry(id=fixed, company="Roche", role="Engineer")
    assert we.id == fixed


def test_legacy_workentry_without_id_backfills_on_load():
    # legacy JSONB rows have no id at all
    we = WorkEntry.model_validate({"company": "Acme", "role": "Lead"})
    assert isinstance(we.id, str)
    uuid.UUID(we.id)


def test_legacy_workentry_without_is_current_loads_as_unknown():
    # #155 — additive JSONB field: old rows have no is_current and must still
    # validate, loading as tri-state "unknown" (None).
    we = WorkEntry.model_validate({"company": "Acme", "role": "Lead", "end_date": None})
    assert we.is_current is None
