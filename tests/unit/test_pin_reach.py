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

"""ADR-077 clauses 3 + 4 — pin reach: the PINNED FACTS block, presence as a
measurement scoped to the pinned entry's tailored twin (never document-wide),
and the carrier partition input for `rank_cuts`.
"""

import uuid

from applire.schemas.application import FactPin
from applire.schemas.cv import (
    TailoredContact,
    TailoredCVData,
    TailoredWorkEntry,
)
from applire.schemas.profile import MasterProfileData, Skill, WorkEntry
from applire.services.pin_reach import (
    bullet_pin_carrier_indices,
    pin_present_in_cv,
    pin_present_in_letter,
    render_pinned_facts_block,
)

ACHIEVEMENT = "Cut deployment time by 70% across 12 teams"


def _profile() -> MasterProfileData:
    return MasterProfileData(
        work_experience=[
            WorkEntry(role="Platform Lead", company="Acme", achievements=[ACHIEVEMENT]),
            WorkEntry(role="Dev", company="Beta GmbH", achievements=["Built the shop"]),
        ],
        skills=[Skill(name="Kubernetes")],
    )


def _pin(profile, *, entry="work0", quote=ACHIEVEMENT, targets=None, stale=False):
    if entry == "work0":
        etype, eid = "work", profile.work_experience[0].id
    elif entry == "work1":
        etype, eid = "work", profile.work_experience[1].id
    else:
        etype, eid = "skill", profile.skills[0].id
    return FactPin(
        pin_id=str(uuid.uuid4()),
        entry_type=etype,
        entry_id=eid,
        quote=quote,
        targets=targets or ["cv", "letter"],
        stale=stale,
    )


def _tailored(profile, *, bullets=None, skills=()) -> TailoredCVData:
    return TailoredCVData(
        contact=TailoredContact(name="X"),
        work_history=[
            TailoredWorkEntry(
                id=profile.work_experience[0].id,
                company="Acme",
                role="Platform Lead",
                bullets=list(bullets or []),
            ),
            TailoredWorkEntry(
                id=profile.work_experience[1].id,
                company="Beta GmbH",
                role="Dev",
                bullets=["Reduced deployment time by 70% across teams"],
            ),
        ],
        skills=list(skills),
    )


# ── PINNED FACTS block (clause 3) ────────────────────────────────────────────


def test_block_renders_active_pins_for_the_target_with_entry_labels():
    profile = _profile()
    pins = [_pin(profile), _pin(profile, entry="skill", quote="Kubernetes")]
    block = render_pinned_facts_block(pins, profile, target="cv", language="en")
    assert "PINNED FACTS" in block
    assert ACHIEVEMENT in block and "Kubernetes" in block
    assert "Acme" in block  # the entry label, so the writer knows the source


def test_block_excludes_stale_and_off_target_pins():
    profile = _profile()
    pins = [
        _pin(profile, stale=True),
        _pin(profile, entry="skill", quote="Kubernetes", targets=["letter"]),
    ]
    assert render_pinned_facts_block(pins, profile, target="cv", language="en") == ""


def test_block_is_empty_without_pins():
    assert render_pinned_facts_block([], _profile(), target="cv", language="en") == ""


# ── Presence measurement (clause 3 — twin-scoped, never document-wide) ───────


def test_work_pin_present_when_its_own_twin_carries_the_quote():
    profile = _profile()
    tailored = _tailored(profile, bullets=[f"Achieved: {ACHIEVEMENT.lower()}"])
    assert pin_present_in_cv(_pin(profile), tailored) is True


def test_work_pin_absent_when_only_another_entry_carries_it():
    # SF-PIN.3: measurement is scoped to the pinned entry's id-resolved twin.
    # Entry 2 carries similar text; the pinned entry 1 does not → NOT present.
    profile = _profile()
    tailored = _tailored(profile, bullets=["Something unrelated"])
    assert pin_present_in_cv(_pin(profile), tailored) is False


def test_skill_pin_measured_against_the_skills_section():
    profile = _profile()
    pin = _pin(profile, entry="skill", quote="Kubernetes")
    assert pin_present_in_cv(pin, _tailored(profile, skills=["Kubernetes, Docker"])) is True
    assert pin_present_in_cv(pin, _tailored(profile, skills=["Python"])) is False


def test_letter_presence_is_containment_over_the_paragraphs():
    from applire.schemas.cover_letter import LetterBody, LetterData

    profile = _profile()
    letter = LetterData(
        body=LetterBody(paragraphs=["I once cut deployment time by 70% across 12 teams."])
    )
    assert pin_present_in_letter(_pin(profile), letter) is True
    letter2 = LetterData(body=LetterBody(paragraphs=["Nothing relevant."]))
    assert pin_present_in_letter(_pin(profile), letter2) is False


# ── Carrier partition input (clause 4) ───────────────────────────────────────


def test_work_pin_marks_the_carrier_bullet_in_its_entry():
    profile = _profile()
    texts = ["Filler", f"Delivered — {ACHIEVEMENT}", "More filler"]
    carriers = bullet_pin_carrier_indices(
        texts, entry_id=profile.work_experience[0].id, pins=[_pin(profile)]
    )
    assert carriers == {1}


def test_a_short_skill_pin_never_immunizes_bullets():
    # SF-PIN.3's over-protection hole: "Kubernetes" pinned once must not make
    # every bullet mentioning it uncuttable.
    profile = _profile()
    texts = ["Migrated the stack to Kubernetes", "Filler"]
    carriers = bullet_pin_carrier_indices(
        texts,
        entry_id=profile.work_experience[0].id,
        pins=[_pin(profile, entry="skill", quote="Kubernetes")],
    )
    assert carriers == set()


def test_stale_and_wrong_entry_pins_mark_nothing():
    profile = _profile()
    texts = [f"Delivered — {ACHIEVEMENT}"]
    assert (
        bullet_pin_carrier_indices(
            texts,
            entry_id=profile.work_experience[0].id,
            pins=[_pin(profile, stale=True)],
        )
        == set()
    )
    assert (
        bullet_pin_carrier_indices(
            texts,
            entry_id=profile.work_experience[1].id,
            pins=[_pin(profile)],
        )
        == set()
    )
