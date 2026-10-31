# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 5 D-2b: in the delivery re-run, two held certificates came
back from a second import as not-applied SKILL items ("…Software Architect",
"ITIL Foundation Level"), and so did one held skill. They had no entry
payload, so they were never adoptable, and the D-2 presence judge never saw
them: it took only items with an entry, and offered only the item's own
section. The hub showed them with nothing but "Zum Abschnitt".

Now an item without an entry goes to the judge by its label. A skill item may
match a held certificate, and a certificate item a held skill. No other pair
crosses a section. Synthetic names only.
"""
from datetime import datetime, timezone

import pytest

from applire.schemas.profile import (
    Certification,
    EnrichmentRecord,
    ImportNotApplied,
    MasterProfileData,
    ProfileMetadata,
    Skill,
)
from applire.services.profile.health import assess_health
from applire.services.profile.not_applied_presence import mark_already_present


def _skill_item(label: str) -> ImportNotApplied:
    return ImportNotApplied(section="skills", label=label, reason="no_op_carried_entry",
                            item_key=f"skills:{label[:8]}")


def _vault(history=()) -> MasterProfileData:
    return MasterProfileData(
        skills=[Skill(id="s1", name="Regulated environments (GMP)")],
        certifications=[Certification(id="c1", name="Certified Service Manager – Foundation Level")],
        education=[],
        metadata=ProfileMetadata(enrichment_history=list(history)),
    )


class _Judge:
    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    async def aparse_json(self, prompt, **kw):
        self.prompts.append(prompt)
        return self.answer


@pytest.mark.asyncio
async def test_a_label_only_skill_item_reaches_the_judge_with_skills_and_certificates_offered():
    items = [_skill_item("Service Manager Foundation"), _skill_item("GMP"), _skill_item("Kubernetes")]
    judge = _Judge({"matches": [
        {"item": "n0", "existing": "c1"},   # a skill item that IS a held certificate
        {"item": "n1", "existing": "s1"},
        {"item": "n2", "existing": None},
    ]})

    assert await mark_already_present(items, _vault(), judge) == 2

    assert [i.already_present_as for i in items] == ["c1", "s1", None]
    [prompt] = judge.prompts
    assert '"certifications"' in prompt and '"skills"' in prompt
    assert '"name": "Service Manager Foundation"' in prompt


@pytest.mark.asyncio
async def test_only_the_skill_certificate_pair_crosses_a_section():
    from applire.schemas.profile import EducationEntry

    vault = _vault()
    vault.education = [EducationEntry(id="e1", institution="Hochschule Nord", degree="M.Sc.")]
    items = [_skill_item("Hochschule Nord")]
    judge = _Judge({"matches": [{"item": "n0", "existing": "e1"}]})

    assert await mark_already_present(items, vault, judge) == 0
    assert items[0].already_present_as is None
    assert '"education"' not in judge.prompts[0]


def test_an_import_whose_skill_items_are_all_held_is_silent():
    items = [_skill_item("Service Manager Foundation").model_copy(update={"already_present_as": "c1"}),
             _skill_item("GMP").model_copy(update={"already_present_as": "s1"})]
    profile = _vault([EnrichmentRecord(timestamp=datetime(2026, 10, 28, tzinfo=timezone.utc), source="linkedin_import", not_applied=items)])

    (issue,) = [i for i in assess_health(profile).issues if i.thread == "not_applied"]

    assert (issue.delivery_class, issue.resolver) == ("S", None)


def test_the_presence_prompt_lets_a_skill_be_a_held_certificate():
    from applire.prompts.profile_decisions import PRESENT_SYSTEM

    text = " ".join(PRESENT_SYSTEM.split())
    assert "A NEW skill may also be one of the EXISTING certificates" in text
    assert "an import of one person's cv did not apply some entries" in text.lower()  # mock fingerprint
