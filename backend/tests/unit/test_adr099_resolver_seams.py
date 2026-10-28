# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
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

"""ADR-099 WP-C × WP-B seams: the review resolver endpoints driven through WP-B's
REAL ``delivery_actions`` (not the contract fake of
``test_adr099_review_resolvers``). Only the model call and the re-audit are
doubled; the persisted section override is the asserted artefact.

Skipped while ``delivery_actions`` still carries the interface stubs."""
from __future__ import annotations

import inspect
from unittest.mock import patch

import pytest

import applire.services.delivery_actions as da
import applire.services.review_actions as ra
import applire.services.review_state as rs
from tests.unit.test_adr099_review_resolvers import _lang_report, _letter_pages
from tests.unit.test_review_endpoints import FakeReaudit, _client, db, seed_letter  # noqa: F401


def _real(fn_name: str) -> bool:
    fn = getattr(da, fn_name, None)
    return fn is not None and "NotImplementedError" not in inspect.getsource(fn)


# B-d1 (reported by WP-C, owned by WP-B): shorten_letter imports REGION_NORMS
# from applire.constants (it lives in applire.norms) — every "kürzen" answers
# 500. Scoped to the defect itself: once the import is fixed the seam runs
# normally (strict — an unexpected pass with the bad import still present fails).
_B_D1 = _real("shorten_letter") and (
    "from applire.constants import REGION_NORMS" in inspect.getsource(da.shorten_letter)
)


@pytest.mark.asyncio
@pytest.mark.skipif(not _real("shorten_letter"), reason="WP-B shorten_letter not merged yet")
@pytest.mark.xfail(_B_D1, reason="B-d1: delivery_actions.shorten_letter imports REGION_NORMS from applire.constants", raises=ImportError, strict=True)
async def test_seam_letter_shorten_through_real_shorten_letter(db):
    paragraphs = ["Erster Absatz.", "Zweiter, sehr langer Absatz über alles.", "Dritter Absatz."]
    cl_id = await seed_letter(db, paragraphs=paragraphs, ats_report=_letter_pages(True))

    async def condense(provider, prompt, **kw):
        return {"body": {"paragraphs": ["Erster Absatz.", "Dritter Absatz."]}}

    with patch("applire.services.cover_letter._condense_call", new=condense), \
            patch.object(ra, "reaudit", new=FakeReaudit([(_letter_pages(False), None)])):
        r = _client(db).post(f"/api/cover-letter/{cl_id}/review/shorten")
    assert r.status_code == 200, r.text
    assert r.json()["still_listed"] is False
    record = await ra.load_document("cover_letter", cl_id, db)
    assert record.section_overrides["body"] == "Erster Absatz.\n\nDritter Absatz."
    assert rs.get_decision(rs.load_state(record.review_state), ra.PAGE_LENGTH_KEY)["action"] == "shortened"


@pytest.mark.asyncio
@pytest.mark.skipif(not _real("translate_spans"), reason="WP-B translate_spans not merged yet")
async def test_seam_letter_translate_through_real_translate_spans(db):
    english = "I led the warehouse team through the system migration and improved our delivery reliability."
    german = "Ich führte das Lagerteam durch die Systemumstellung und verbesserte unsere Lieferzuverlässigkeit."
    paragraphs = ["Sehr gerne bewerbe ich mich bei Ihnen auf die ausgeschriebene Stelle als Teamleiter.", english]
    cl_id = await seed_letter(db, paragraphs=paragraphs, ats_report=_lang_report("cover_letter", True))
    calls: list[str] = []

    async def acomplete(provider, name, prompt, **kw):
        calls.append(name)
        return german

    with patch("applire.prompts.preamble.acomplete", new=acomplete), \
            patch.object(ra, "reaudit", new=FakeReaudit([(_lang_report("cover_letter", False), None)])):
        r = _client(db).post(f"/api/cover-letter/{cl_id}/review/translate", json={})
    assert r.status_code == 200, r.text
    assert calls == ["review_delivery:translate"]
    record = await ra.load_document("cover_letter", cl_id, db)
    body = record.section_overrides["body"]
    assert german in body and english not in body
    assert paragraphs[0] in body
    assert rs.get_decision(rs.load_state(record.review_state), ra.SEND_LANGUAGE_KEY)["action"] == "translated"
