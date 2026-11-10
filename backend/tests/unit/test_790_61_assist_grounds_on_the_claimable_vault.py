# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""790:61 (Hazelnut build 8) — the CV-assist suggestion is grounded against the
CLAIMABLE vault, the Oracle's own index (ADR-061 cl. 2).

The suggestion is text the candidate pastes into a CV section, so it is a claim
surface. Before, `_ground_suggestion` indexed the raw profile, and an entry that is
only `unconfirmed` (or `denied`) backed a suggestion naming it: "ISO 27001" passed
on an unconfirmed "ISO 27001 Lead Auditor" certification, while the delivery-time
Oracle (claimable-only) grades the same sentence `unbacked`. Synthetic data only.
"""
from __future__ import annotations

import copy
import uuid

import pytest

from applire.services.cv_assist import _ground_suggestion

_PROFILE = {
    "personal_info": {"full_name": "Mara Lind"},
    "work_experience": [{
        "id": "11111111-1111-1111-1111-111111111111",
        "company": "Kranich Daten GmbH",
        "role": "IT-Sicherheitsbeauftragte",
        "start_date": "2019-01",
        "responsibilities": ["Interne Audits für das Informationssicherheits-Managementsystem vorbereitet."],
    }],
    "skills": [{"name": "Risikoanalyse", "status": "confirmed"}],
    "certifications": [{"name": "ISO 27001 Lead Auditor", "status": "unconfirmed"}],
}

_SUGGESTION = "Interne Audits nach ISO 27001 für das Informationssicherheits-Managementsystem vorbereitet."


class _Record:
    def __init__(self, profile):
        self.profile_json = profile


class _Result:
    def __init__(self, record):
        self._record = record

    def scalar_one_or_none(self):
        return self._record


class _DB:
    def __init__(self, profile):
        self._record = _Record(profile)

    async def execute(self, _stmt):
        return _Result(self._record)


async def _ground(profile):
    return await _ground_suggestion(_SUGGESTION, _DB(profile), session_evidence=[], owner=uuid.uuid4())


@pytest.mark.asyncio
async def test_an_unconfirmed_only_entry_does_not_back_a_suggestion():
    kept, withheld = await _ground(_PROFILE)
    assert withheld == 1, kept
    assert "ISO 27001" not in kept


@pytest.mark.asyncio
async def test_the_same_entry_confirmed_backs_it():
    profile = copy.deepcopy(_PROFILE)
    profile["certifications"][0]["status"] = "confirmed"
    kept, withheld = await _ground(profile)
    assert withheld == 0, kept
    assert "ISO 27001" in kept


@pytest.mark.asyncio
async def test_the_candidates_own_fresh_answer_still_backs_it():
    """W-2 (SF-ASSIST.2) is unchanged: what the candidate just typed is evidence."""
    kept, withheld = await _ground_suggestion(
        _SUGGESTION, _DB(_PROFILE), owner=uuid.uuid4(),
        session_evidence=[("session.answer", "Ich habe die Audits nach ISO 27001 vorbereitet.")],
    )
    assert withheld == 0, kept
