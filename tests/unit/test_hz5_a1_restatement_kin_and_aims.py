# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 5, D-17 residue (delivery run 4, rep b).

(a) An answer "the 70 % are a target, not reached yet" was written as an
ACHIEVEMENT next to the held target line. The reconcile prompt now says a
target the answer calls not reached never goes in achievements. The
restatement check's HELD list now carries the entry's ``not_yet_reached``
aims, and its prompt says "not reached yet" is no new fact.

(b) "Established company standards ... no major audit deviations" was added
to the QC LIMS project while the position the project belongs to already
held it. HELD now also carries the bullets of the entry's kin: a project's
position, a position's projects.

Replay (gpt-6-luna, thinking off) of the two run-4 turns with the old code
and then the new code: the target-as-achievement held back 0/2 → 4/4, the
project's standards bullet held back 0/2 → 4/4. The r3 bench is unchanged
(new-for-the-entry bullets written 9/9, controls 0/32 held back).
"""
import pytest

from applire.prompts.reconcile import RECONCILE_SYSTEM_PROMPT
from applire.prompts.restatement import RESTATEMENT_SYSTEM_PROMPT
from applire.schemas.profile import MasterProfileData, ProjectEntry, WorkEntry
from applire.services.profile.reconcile.ops import AddBullets
from applire.services.profile.reconcile.restatement import hold_back_restatements

W1 = "11111111-1111-4111-8111-111111111111"
W2 = "22222222-2222-4222-8222-222222222222"
P1 = "33333333-3333-4333-8333-333333333333"
ROLE = "Set validation standards for all lab software, with no audit findings since."
AIM = "Aims to halve the release cycle of the lab platform."


def _profile() -> MasterProfileData:
    return MasterProfileData(
        work_experience=[
            WorkEntry(id=W1, company="Nordwerk AG", role="Systems Engineer", start_date="2018-10",
                      end_date="2021-05", responsibilities=[ROLE], not_yet_reached=[AIM]),
            WorkEntry(id=W2, company="Nordwerk AG", role="Tester", start_date="2016-01",
                      end_date="2018-09", responsibilities=["Tested the order system."]),
        ],
        projects=[ProjectEntry(id=P1, name="Lab rollout", associated_experience=W1,
                               responsibilities=["Rolled the lab system out to three sites."])],
    )


class _Recorder:
    def __init__(self):
        self.prompts = []

    async def aparse_json(self, prompt, **kw):
        self.prompts.append(prompt)
        return {"items": []}


@pytest.mark.asyncio
async def test_a_project_entry_is_checked_against_its_positions_bullets():
    rec = _Recorder()
    await hold_back_restatements(_profile(), [AddBullets(target=P1, achievements=["Set the validation standards."])], rec)

    [prompt] = rec.prompts
    assert ROLE in prompt and "Rolled the lab system out" in prompt
    assert "Tested the order system." not in prompt


@pytest.mark.asyncio
async def test_a_position_entry_is_checked_against_its_projects_and_its_aims():
    rec = _Recorder()
    await hold_back_restatements(_profile(), [AddBullets(target=W1, responsibilities=["Targets a shorter release cycle."])], rec)

    [prompt] = rec.prompts
    assert "Rolled the lab system out to three sites." in prompt
    assert AIM in prompt


@pytest.mark.asyncio
async def test_an_entry_without_kin_sees_only_its_own_bullets():
    rec = _Recorder()
    await hold_back_restatements(_profile(), [AddBullets(target=W2, responsibilities=["Wrote test plans."])], rec)

    [prompt] = rec.prompts
    assert ROLE not in prompt and "Rolled the lab system out" not in prompt


@pytest.mark.asyncio
async def test_a_receipt_names_the_kin_bullet_it_restates():
    class _Judge:
        async def aparse_json(self, prompt, **kw):
            return {"items": [{"new": "e1.n1", "added": "", "held": "e1.h2"}]}

    profile = _profile()
    kept, [receipt] = await hold_back_restatements(
        profile, [AddBullets(target=P1, achievements=["Set the validation standards, no audit findings."])], _Judge()
    )
    assert kept == []
    assert receipt.entity_id == P1 and receipt.restates == ROLE


def test_the_prompts_carry_the_rules():
    flat = " ".join(RECONCILE_SYSTEM_PROMPT.split())
    assert "A TARGET IS NOT AN ACHIEVEMENT: a goal, target or plan the answer says is not reached yet never goes in achievements." in flat
    assert "in any list including not_yet_reached, it is already there" in flat
    r = " ".join(RESTATEMENT_SYSTEM_PROMPT.split())
    assert "the bullets of the position a project belongs to or of a position's projects" in r
    assert "That a held target or goal is not reached yet is not a fact" in r
    assert "70" not in RECONCILE_SYSTEM_PROMPT[RECONCILE_SYSTEM_PROMPT.index("A TARGET IS NOT"):][:400]
