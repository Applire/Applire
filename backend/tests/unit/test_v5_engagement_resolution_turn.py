# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Founder ruling V-5 (2026-09-09) — the deterministic engagement resolution turn.

When `classify_engagement_dupe` returned AMBIGUOUS the applier parked a question
and created nothing — correct, "ask, never guess". Nothing then applied the
ANSWER: `_apply_resolve_confirmation` is bookkeeping by design and
`session._apply_interview_confirmation` returned early for anything that was not
a SKILL confirmation. The candidate answered, the park closed with a receipt
saying their choice was recorded, and the station / project / volunteering — and
any bullets carried onto `context["pending_bullets"]` — were gone.

**One seam test per family**, because the three appliers were already three
copies of one near-dupe block and a resolution that landed on one of them would
have been the #177 asymmetry again. Reverting the waiver in any single family
must redden that family's own test and no other.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_backend = Path(__file__).resolve().parents[2]
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.profile import MasterProfileData, ProfileMetadata  # noqa: E402
from applire.services.profile.reconcile.apply import (  # noqa: E402
    UserConfirmedEngagement,
    apply_ops,
)
from applire.services.profile.reconcile.ops import (  # noqa: E402
    AddBullets,
    UpsertProject,
    UpsertVolunteer,
    UpsertWork,
)

BULLET = "Verantwortlich für die Qualifizierung der MES-Anbindung"


def _profile(**sections) -> MasterProfileData:
    return MasterProfileData.model_validate(
        {
            "personal_info": {"name": "Lena Fischer"},
            "metadata": ProfileMetadata().model_dump(mode="json"),
            **sections,
        }
    )


# ── the three families, ambiguity → answer → landed ──────────────────────────

FAMILIES = {
    "work_experience": (
        {"work_experience": [{"id": "e1", "company": "Helvetia Pharma GmbH", "role": "Systems Engineer"}]},
        lambda: UpsertWork(ref="n1", company="Helvetia Pharma", role="Systems Engineer"),
    ),
    "projects": (
        {"projects": [{"id": "e1", "name": "LucaNet Konsolidierung", "role": "Lead"}]},
        lambda: UpsertProject(ref="n1", name="LucaNet Konsolidierung", role="Lead"),
    ),
    "volunteer_activities": (
        {"volunteer_activities": [{"id": "e1", "organization": "Tafel Nord e.V.", "role": "Helfer"}]},
        lambda: UpsertVolunteer(ref="n1", organization="Tafel Nord", role="Helfer"),
    ),
}


def _entries(profile: MasterProfileData, section: str) -> list:
    return getattr(profile, section)


@pytest.mark.parametrize("section", list(FAMILIES))
def test_the_ambiguous_op_parks_a_question_carrying_the_existing_ID_not_a_label(section):
    """The precondition, and the #669 property the resolution rests on: the
    confirmation records the existing entity's ID. Resolving on a rendered label
    would undo exactly what the option-key build was for."""
    vault, make_op = FAMILIES[section]
    result = apply_ops(_profile(**vault), [make_op(), AddBullets(target="n1", responsibilities=[BULLET])], "interview")

    assert len(result.pending_confirmations) == 1
    ctx = result.pending_confirmations[0].context
    assert ctx["section"] == section
    assert ctx["existing_ids"] == ["e1"]
    assert ctx["incoming"]["ref"] == "n1"
    assert result.pending_confirmations[0].option_keys == ["merge", "distinct"]
    assert len(_entries(result.profile, section)) == 1


@pytest.mark.parametrize("section", list(FAMILIES))
def test_answering_DISTINCT_creates_the_entry_with_its_bullets(section):
    """"Different — keep both". The guard that asked is skipped, not re-run:
    re-running it would re-ask the question the candidate just answered."""
    vault, make_op = FAMILIES[section]
    result = apply_ops(
        _profile(**vault),
        [make_op(), AddBullets(target="n1", responsibilities=[BULLET])],
        "interview",
        user_confirmed_engagement=UserConfirmedEngagement(ref="n1", decision="distinct"),
    )
    entries = _entries(result.profile, section)
    assert len(entries) == 2
    created = [e for e in entries if getattr(e, "id", None) != "e1"]
    assert len(created) == 1
    assert created[0].responsibilities == [BULLET]
    assert result.pending_confirmations == []


@pytest.mark.parametrize("section", list(FAMILIES))
def test_answering_MERGE_folds_into_the_named_id_with_its_bullets(section):
    """"Same — merge them". The id comes from the confirmation's context, never
    from re-matching a label."""
    vault, make_op = FAMILIES[section]
    result = apply_ops(
        _profile(**vault),
        [make_op(), AddBullets(target="n1", responsibilities=[BULLET])],
        "interview",
        user_confirmed_engagement=UserConfirmedEngagement(ref="n1", decision="merge", target_id="e1"),
    )
    entries = _entries(result.profile, section)
    assert len(entries) == 1
    assert entries[0].id == "e1"
    assert entries[0].responsibilities == [BULLET]


@pytest.mark.parametrize("section", list(FAMILIES))
def test_the_waiver_is_keyed_to_ONE_op_and_leaves_the_rest_adjudicated(section):
    """A bare boolean would make the bypass's reach depend on what else the
    caller happened to put in the batch — the reason `UserConfirmedSkill` is
    keyed too."""
    vault, make_op = FAMILIES[section]
    other = {
        "work_experience": UpsertWork(ref="n2", company="Helvetia Pharma", role="Systems Engineer"),
        "projects": UpsertProject(ref="n2", name="LucaNet Konsolidierung", role="Lead"),
        "volunteer_activities": UpsertVolunteer(ref="n2", organization="Tafel Nord", role="Helfer"),
    }[section]
    result = apply_ops(
        _profile(**vault),
        [make_op(), other],
        "interview",
        user_confirmed_engagement=UserConfirmedEngagement(ref="n1", decision="distinct"),
    )
    # n1 landed; n2 was still adjudicated and still asks.
    assert len(_entries(result.profile, section)) == 2
    assert len(result.pending_confirmations) == 1
    assert result.pending_confirmations[0].context["incoming"]["ref"] == "n2"


# ── the merge's field policy ─────────────────────────────────────────────────


def test_a_merge_keeps_the_existing_value_and_receipts_the_divergence():
    """Founder ruling V-5: conflicting fields keep the existing value and the
    divergence becomes a dispute. `_fill_empties` already did the first half in
    silence; the second half is what makes the drop visible."""
    vault = {"work_experience": [{"id": "e1", "company": "Helvetia Pharma GmbH",
                                  "role": "Systems Engineer", "start_date": "2011-04"}]}
    result = apply_ops(
        _profile(**vault),
        [UpsertWork(ref="n1", company="Helvetia Pharma", role="Systems Engineer",
                    start_date="2012-01", location="Basel")],
        "interview",
        user_confirmed_engagement=UserConfirmedEngagement(ref="n1", decision="merge", target_id="e1"),
    )
    entry = result.profile.work_experience[0]
    assert entry.start_date == "2011-04"        # existing wins
    assert entry.location == "Basel"            # an EMPTY field is still filled
    disputes = [(c.section, c.field, c.existing_value, c.incoming_value) for c in result.conflicts]
    assert disputes == [("work_experience", "start_date", "2011-04", "2012-01")]


#: Adversarial finding (2026-09-09, WP-adv-vault): `_apply_upsert_project` and
#: `_apply_upsert_volunteer` did not call `_record_merge_divergence` at all —
#: only `_apply_upsert_work` did — so a candidate-confirmed MERGE on those two
#: families silently kept the existing value with NO conflict receipt, exactly
#: the #177 asymmetry this ADR's own commit message warns against. Reproduced
#: with a plain script before the fix: `conflicts == []` on both families for a
#: divergent `start_date`. Fixed by wiring the same call in both appliers.
_DIVERGENCE_FAMILIES = {
    "projects": (
        {"projects": [{"id": "e1", "name": "LucaNet Konsolidierung", "role": "Lead",
                       "start_date": "2011-04"}]},
        lambda: UpsertProject(ref="n1", name="LucaNet Konsolidierung", role="Senior Lead",
                               start_date="2012-01", url="https://example.com"),
    ),
    "volunteer_activities": (
        {"volunteer_activities": [{"id": "e1", "organization": "Tafel Nord e.V.",
                                    "role": "Helfer", "start_date": "2011-04"}]},
        lambda: UpsertVolunteer(ref="n1", organization="Tafel Nord", role="Koordinator",
                                 start_date="2012-01", description="Lebensmittel verteilen"),
    ),
}


@pytest.mark.parametrize("section", list(_DIVERGENCE_FAMILIES))
def test_a_merge_on_projects_or_volunteer_also_receipts_the_divergence(section):
    """The work_experience field policy (previous test) generalises to the
    other two engagement families — it must, or the divergence receipt is
    itself the #177 asymmetry. `role` is checked too: unlike WorkEntry,
    neither ProjectEntry nor VolunteerActivity has a `role_aliases` escape
    hatch, so a differing role has no OTHER receipt path if this one misses it.
    """
    vault, make_op = _DIVERGENCE_FAMILIES[section]
    result = apply_ops(
        _profile(**vault),
        [make_op()],
        "interview",
        user_confirmed_engagement=UserConfirmedEngagement(ref="n1", decision="merge", target_id="e1"),
    )
    entry = _entries(result.profile, section)[0]
    assert entry.start_date == "2011-04"  # existing wins
    assert entry.role in ("Lead", "Helfer")  # existing role wins too
    disputes = {(c.section, c.field) for c in result.conflicts}
    assert (section, "start_date") in disputes
    assert (section, "role") in disputes


def test_a_merge_whose_target_was_edited_away_creates_rather_than_loses():
    """The safe direction. Creating a duplicate the candidate can merge later is
    recoverable; silently dropping their answer a second time is not."""
    result = apply_ops(
        _profile(work_experience=[{"id": "e1", "company": "Helvetia Pharma GmbH", "role": "Systems Engineer"}]),
        [UpsertWork(ref="n1", company="Helvetia Pharma", role="Systems Engineer")],
        "interview",
        user_confirmed_engagement=UserConfirmedEngagement(ref="n1", decision="merge", target_id="gone"),
    )
    assert len(result.profile.work_experience) == 2


# ── the door seam: both Statement doors reach the same resolution ────────────


@pytest.mark.parametrize("option_key,expected", [("merge", "merge"), ("distinct", "distinct")])
def test_the_decision_resolves_on_the_stable_key_not_on_the_rendered_text(option_key, expected):
    """#669's property, inherited: a German render and an English render of the
    same option must reach the same decision."""
    from applire.services.session import _engagement_decision

    assert _engagement_decision("Dieselbe Position — zusammenführen", option_key) == expected
    assert _engagement_decision("Same position — merge them", option_key) == expected


def test_without_a_key_the_fallback_fails_toward_creating_not_merging():
    """Back-compat for a confirmation persisted before #669. Merging two real
    positions into one is unrecoverable; a duplicate is not."""
    from applire.services.session import _engagement_decision

    assert _engagement_decision("Same position — merge them", None) == "merge"
    assert _engagement_decision("Dieselbe Position — zusammenführen", None) == "merge"
    assert _engagement_decision("something the matcher has never seen", None) == "distinct"
    assert _engagement_decision("", None) == "distinct"


@pytest.mark.parametrize("section", list(FAMILIES))
def test_a_context_from_an_older_release_is_refused_rather_than_guessed(section):
    """A parked confirmation whose `incoming` cannot rebuild an op must not be
    half-applied. `_apply_engagement_confirmation` returns False and the
    applier's WARNING remains the record."""
    import asyncio

    from applire.services.session import _apply_engagement_confirmation

    applied = asyncio.run(
        _apply_engagement_confirmation(
            None, None, {"section": section, "incoming": {"nonsense": True}}, "x",
            session_id="s1", option_key="distinct",
        )
    )
    assert applied is False


def test_a_non_engagement_section_is_not_claimed_by_this_arm():
    """`personal_info` / a skill confirmation must fall through untouched."""
    import asyncio

    from applire.services.session import _apply_engagement_confirmation

    for ctx in ({"section": "personal_info", "incoming": {}}, {"incoming_skill": "SAP PP"}, {}):
        assert asyncio.run(
            _apply_engagement_confirmation(None, None, ctx, "x", session_id="s1")
        ) is False
