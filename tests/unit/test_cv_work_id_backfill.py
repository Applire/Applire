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

"""E042 Task 1.3 fix round (US238): _backfill_work_ids.

The single-call fast path's LLM schema omits ``id`` on work entries, so
TailoredWorkEntry.id lands as "" — and the condense loop's budget lookup (keyed by
profile WorkEntry.id) misses every role. This deterministic pass back-fills the
profile ids onto the tailored entries (company+role identity, order-based fallback)
so BOTH generation paths satisfy the budget-key seam.
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.cv import TailoredCVData  # noqa: E402
from applire.services.cv import _backfill_work_ids  # noqa: E402


def _tailored(entries) -> TailoredCVData:
    return TailoredCVData.model_validate({
        "contact": {"name": "Anna Bauer"},
        "work_history": entries,
        "skills": [],
    })


def _profile(entries) -> dict:
    return {"work_experience": entries}


def test_backfills_by_company_and_role():
    tailored = _tailored([
        {"company": "Acme GmbH", "role": "Engineer", "start_date": "2020", "bullets": ["a"]},
        {"company": "Beta AG", "role": "Developer", "start_date": "2015", "bullets": ["b"]},
    ])
    profile = _profile([
        {"id": "uuid-beta", "company": "Beta AG", "role": "Developer"},
        {"id": "uuid-acme", "company": "Acme GmbH", "role": "Engineer"},
    ])
    out = _backfill_work_ids(tailored, profile)
    assert out.work_history[0].id == "uuid-acme"
    assert out.work_history[1].id == "uuid-beta"


def test_matching_is_case_and_whitespace_insensitive():
    tailored = _tailored([
        {"company": "  acme gmbh ", "role": "ENGINEER", "start_date": "2020", "bullets": []},
    ])
    profile = _profile([{"id": "uuid-1", "company": "Acme GmbH", "role": "Engineer"}])
    out = _backfill_work_ids(tailored, profile)
    assert out.work_history[0].id == "uuid-1"


def test_existing_ids_are_never_overwritten():
    tailored = _tailored([
        {"id": "kept", "company": "Acme GmbH", "role": "Engineer", "start_date": "2020", "bullets": []},
    ])
    profile = _profile([{"id": "uuid-1", "company": "Acme GmbH", "role": "Engineer"}])
    out = _backfill_work_ids(tailored, profile)
    assert out.work_history[0].id == "kept"


def test_all_ids_present_is_a_noop_object():
    tailored = _tailored([
        {"id": "x", "company": "Acme", "role": "Eng", "start_date": "2020", "bullets": []},
    ])
    out = _backfill_work_ids(tailored, _profile([{"id": "x", "company": "Acme", "role": "Eng"}]))
    assert out is tailored


def test_ambiguous_duplicate_pairs_fall_back_to_order():
    # Two identical company+role pairs (a genuine re-hire) — name matching is
    # ambiguous; equal counts + enforced reverse-chron order make positional
    # pairing the deterministic tie-break.
    tailored = _tailored([
        {"company": "Acme GmbH", "role": "Engineer", "start_date": "2022", "bullets": ["new"]},
        {"company": "Acme GmbH", "role": "Engineer", "start_date": "2016", "bullets": ["old"]},
    ])
    profile = _profile([
        {"id": "uuid-new", "company": "Acme GmbH", "role": "Engineer"},
        {"id": "uuid-old", "company": "Acme GmbH", "role": "Engineer"},
    ])
    out = _backfill_work_ids(tailored, profile)
    assert out.work_history[0].id == "uuid-new"
    assert out.work_history[1].id == "uuid-old"


def test_no_match_and_count_mismatch_leaves_id_empty():
    # Safety: an unmatchable entry with unequal counts must NOT get a guessed id
    # (a wrong id would apply the wrong role's budget).
    tailored = _tailored([
        {"company": "Unknown Corp", "role": "Wizard", "start_date": "2020", "bullets": []},
        {"company": "Acme GmbH", "role": "Engineer", "start_date": "2018", "bullets": []},
    ])
    profile = _profile([{"id": "uuid-1", "company": "Acme GmbH", "role": "Engineer"}])
    out = _backfill_work_ids(tailored, profile)
    assert out.work_history[0].id == ""
    assert out.work_history[1].id == "uuid-1"


def test_never_assigns_the_same_profile_id_twice():
    # One profile entry, two tailored entries claiming the same company+role:
    # the id may be used once; the second stays empty rather than duplicating.
    tailored = _tailored([
        {"company": "Acme GmbH", "role": "Engineer", "start_date": "2020", "bullets": []},
        {"company": "Acme GmbH", "role": "Engineer", "start_date": "2018", "bullets": []},
        {"company": "Beta AG", "role": "Dev", "start_date": "2015", "bullets": []},
    ])
    profile = _profile([
        {"id": "uuid-acme", "company": "Acme GmbH", "role": "Engineer"},
        {"id": "uuid-beta", "company": "Beta AG", "role": "Dev"},
    ])
    out = _backfill_work_ids(tailored, profile)
    ids = [w.id for w in out.work_history]
    assert ids.count("uuid-acme") == 1
    assert ids[2] == "uuid-beta"


def test_input_is_not_mutated():
    tailored = _tailored([
        {"company": "Acme GmbH", "role": "Engineer", "start_date": "2020", "bullets": []},
    ])
    profile = _profile([{"id": "uuid-1", "company": "Acme GmbH", "role": "Engineer"}])
    _backfill_work_ids(tailored, profile)
    assert tailored.work_history[0].id == ""


def test_empty_profile_or_history_is_a_noop():
    tailored = _tailored([])
    assert _backfill_work_ids(tailored, _profile([])) is tailored
    tailored2 = _tailored([{"company": "A", "role": "B", "start_date": "2020", "bullets": []}])
    assert _backfill_work_ids(tailored2, {}) is tailored2
