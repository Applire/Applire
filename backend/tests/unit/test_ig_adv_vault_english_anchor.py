# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Invite-gate adversarial pass (vault): an English answer "At Weberit I …" names
the employer. The short-form matcher skipped a lead followed by a capitalised
word ("Weberit Automotive" is another company), and English's "I" is always
capitalised, so every English sentence of that shape anchored nothing and the
employer guard was blind on it. Synthetic fixture."""
from __future__ import annotations

from applire.schemas.profile import MasterProfileData, WorkEntry
from applire.services.profile.reconcile.attribution import (
    _anchor_company,
    _company_candidates,
    enforce_attribution,
)
from applire.services.profile.reconcile.ops import AddBullets, RequestConfirmation

_W, _R = "w-weberit", "w-rasselstein"


def _profile() -> MasterProfileData:
    return MasterProfileData(work_experience=[
        WorkEntry(id=_W, company="Weberit Kunststofftechnik GmbH", role="Produktionsleiter",
                  start_date="2017-04", is_current=True, responsibilities=["Zwei Fertigungsbereiche."]),
        WorkEntry(id=_R, company="Rasselstein Umformtechnik GmbH", role="Schichtleiter",
                  start_date="2011-08", end_date="2017-03", responsibilities=["Führung einer Schicht."]),
    ])


def test_an_english_first_person_sentence_names_the_employer():
    cands = _company_candidates(_profile())
    assert _anchor_company("At Weberit I prepared the ISO 45001 certification.", cands)[0] == "Weberit Kunststofftechnik"
    assert _anchor_company("At Weberit Automotive we built cars.", cands) is None  # another name stays out


def test_the_guard_holds_an_english_answers_bullets_on_the_other_employer():
    out = enforce_attribution(
        [AddBullets(target=_R, responsibilities=["Prepared the ISO 45001 certification for production."])],
        profile=_profile(),
        new_info={"gap": "g", "question": "q", "answer": "At Weberit I prepared the ISO 45001 certification for production."},
        source="interview",
    )
    assert any(isinstance(o, RequestConfirmation) for o in out)
