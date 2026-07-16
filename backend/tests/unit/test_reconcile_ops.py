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

"""ADR-046 — schema tests for the typed reconciliation op vocabulary."""
from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from applire.services.profile.reconcile.ops import AddBullets, RequestConfirmation


def test_imports():
    # sanity import of the union + result models
    from applire.services.profile.reconcile.ops import ReconcileOp, ReconcileResult  # noqa: F401


def test_discriminated_union_dispatches_by_op():
    from applire.services.profile.reconcile.ops import ReconcileOp, UpsertWork

    adapter = TypeAdapter(ReconcileOp)
    parsed = adapter.validate_python(
        {"op": "upsert_work", "ref": "w1", "target": None, "company": "Applire", "role": "Founder"}
    )
    assert isinstance(parsed, UpsertWork)
    assert parsed.ref == "w1"
    assert parsed.target is None


def test_all_op_literals_present():
    from applire.services.profile.reconcile.ops import ReconcileOp

    adapter = TypeAdapter(ReconcileOp)
    samples = [
        {"op": "upsert_work", "ref": "w1", "company": "X", "role": "Y"},
        {"op": "upsert_project", "ref": "p1", "name": "N"},
        {"op": "upsert_volunteer", "ref": "v1", "organization": "O", "role": "R"},
        {"op": "add_bullets", "target": "w1"},
        {"op": "upsert_skill", "name": "Python"},
        {"op": "upsert_certification", "name": "AWS"},
        {"op": "upsert_language", "language": "German"},
        {"op": "upsert_education", "institution": "TUM", "degree": "BSc"},
        {"op": "set_field", "target": "w1", "field": "end_date", "value": "2020"},
        {"op": "set_personal_info", "field": "name", "value": "Max"},
        {"op": "set_summary", "lang": "de", "text": "Hallo"},
        {"op": "flag_conflict", "target": "w1", "field": "company", "existing": "A", "incoming": "B"},
        {"op": "request_confirmation", "question": "Which one?"},
    ]
    for s in samples:
        adapter.validate_python(s)


def test_reconcile_result_holds_ops_and_ambiguities():
    from applire.services.profile.reconcile.ops import ReconcileResult

    r = ReconcileResult.model_validate(
        {
            "ops": [{"op": "upsert_skill", "name": "Go"}],
            "ambiguities": [{"op": "request_confirmation", "question": "Merge X with Y?"}],
        }
    )
    assert len(r.ops) == 1
    assert len(r.ambiguities) == 1
    assert isinstance(r.ambiguities[0], RequestConfirmation)


def test_defaults_are_independent_lists():
    a = AddBullets(target="w1")
    b = AddBullets(target="w2")
    a.responsibilities.append("x")
    assert b.responsibilities == []


def test_unknown_op_rejected():
    from applire.services.profile.reconcile.ops import ReconcileOp

    adapter = TypeAdapter(ReconcileOp)
    with pytest.raises(ValidationError):
        adapter.validate_python({"op": "delete_everything", "target": "w1"})


def test_upsert_publication_op_parses():
    from applire.services.profile.reconcile.ops import UpsertPublication

    op = UpsertPublication(title="Model-based Testing of Embedded Systems", venue="ETFA",
                           published_date="2019", type="publication")
    assert op.op == "upsert_publication"


def test_upsert_work_carries_is_current_marker():
    # #155 — the op vocabulary can record "current position" without an end_date.
    from applire.services.profile.reconcile.ops import ReconcileOp, UpsertWork

    adapter = TypeAdapter(ReconcileOp)
    parsed = adapter.validate_python(
        {"op": "upsert_work", "ref": "w1", "company": "X", "role": "Y", "is_current": True}
    )
    assert isinstance(parsed, UpsertWork)
    assert parsed.is_current is True
    # tri-state default: omitted → None (unknown)
    plain = adapter.validate_python({"op": "upsert_work", "ref": "w1", "company": "X", "role": "Y"})
    assert plain.is_current is None
