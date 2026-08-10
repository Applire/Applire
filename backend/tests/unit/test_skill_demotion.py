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

"""#485 — a candidate's retraction demotes a persisted `confirmed` skill to the
new `denied` vault status (ADR-061 amended 2026-08-08 / ADR-063 clause 8(e)
amended 2026-08-08).

The four properties this file pins:

1. **The op.** ``DemoteSkill`` is a member of the ADR-046 op vocabulary and
   flows through ``apply_ops`` like every other op (ADR-066) — mark, don't
   delete: the entry keeps its name, provenance and history, only ``status``
   moves, and the move is receipted.
2. **The emission rule** is an *assert*-class act, so it fires on a DECLARED
   denial only (``stance.declared_denial_matches``, longest match first) and
   NEVER on containment: retracting "Tailwind CSS" leaves the vault's "CSS"
   alone. It lives in the shared reconcile core (``engine.reconcile``), so
   every door gets it from one implementation.
3. **Nothing leaves `denied`** except the explicit ADR-059 un-denial act, which
   does not exist yet — so in code, no ordinary op ever promotes a `denied`
   entry. A CV re-import naming the retracted skill must not resurrect it.
4. **Every claim surface excludes `denied`**, through the one shared predicate.
"""
from __future__ import annotations

from typing import Any

import pytest

from applire.schemas.profile import (
    Certification,
    Language,
    MasterProfileData,
    Skill,
)
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.engine import reconcile
from applire.services.profile.reconcile.ops import (
    DemoteSkill,
    UpsertCertification,
    UpsertLanguage,
    UpsertSkill,
)
from applire.services.profile.reconcile.stance import (
    demote_ops_for_denials,
    exclude_unconfirmed,
)

SOURCE = "testimony"


class _StubProvider:
    """Returns one canned reconcile payload; absorbs the full provider ABC."""

    def __init__(self, payload: Any) -> None:
        self.payload = payload

    async def aparse_json(self, prompt: str, **kwargs: Any) -> Any:
        return self.payload


def _vault_with(*skills: Skill) -> MasterProfileData:
    return MasterProfileData(skills=list(skills))


# ── 1. The op ────────────────────────────────────────────────────────────────


def test_demote_skill_op_moves_a_confirmed_skill_to_denied() -> None:
    profile = _vault_with(Skill(name="Kubernetes", status="confirmed"))

    result = apply_ops(profile, [DemoteSkill(name="Kubernetes")], SOURCE)

    assert [s.status for s in result.profile.skills] == ["denied"]


def test_demote_marks_rather_than_deletes_and_keeps_the_entry_history() -> None:
    profile = _vault_with(
        Skill(
            name="Kubernetes",
            category="technical",
            proficiency="advanced",
            experience_refs=["w1", "w2"],
            status="confirmed",
        )
    )

    result = apply_ops(profile, [DemoteSkill(name="Kubernetes")], SOURCE)

    kept = result.profile.skills[0]
    assert kept.name == "Kubernetes"
    assert kept.category == "technical"
    assert kept.proficiency == "advanced"
    assert kept.experience_refs == ["w1", "w2"]
    assert kept.status == "denied"


def test_demotion_writes_its_own_receipt() -> None:
    """ADR-059 clause 1 — negative testimony is receipted like positive."""
    profile = _vault_with(Skill(name="Kubernetes", status="confirmed"))

    result = apply_ops(profile, [DemoteSkill(name="Kubernetes")], SOURCE)

    assert len(result.demotions) == 1
    receipt = result.demotions[0]
    assert receipt.section == "skills"
    assert receipt.field == "status"
    assert receipt.old_value == "confirmed"
    assert receipt.new_value == "denied"


def test_a_demotion_is_receipted_but_never_a_gap_addressing_change() -> None:
    """`applied.changes` is read by four gates that all mean "this turn produced
    positive, gap-addressing content" — the wire status, the interview's
    `addressed` flag, and (sharpest) the agent door's `upgrade=` ledger gate. A
    demotion is the OPPOSITE of that, so it is receipted on its own list, the
    same separation #231 already makes for denial receipts. F8: denying a skill
    must never read as "resolved this gap"."""
    profile = _vault_with(Skill(name="Kubernetes", status="confirmed"))

    result = apply_ops(profile, [DemoteSkill(name="Kubernetes")], SOURCE)

    assert result.changes == []
    assert len(result.demotions) == 1


def test_demote_op_is_idempotent_on_an_already_denied_skill() -> None:
    profile = _vault_with(Skill(name="Kubernetes", status="denied"))

    result = apply_ops(profile, [DemoteSkill(name="Kubernetes")], SOURCE)

    assert result.profile.skills[0].status == "denied"
    assert result.changes == []
    assert result.demotions == []


def test_demote_op_naming_no_vault_skill_is_a_no_op() -> None:
    profile = _vault_with(Skill(name="Kubernetes", status="confirmed"))

    result = apply_ops(profile, [DemoteSkill(name="Terraform")], SOURCE)

    assert result.profile.skills[0].status == "confirmed"
    assert result.changes == []
    assert result.demotions == []


# ── 2. The emission rule (declared matches only) ─────────────────────────────


def test_declared_retraction_of_a_confirmed_skill_emits_a_demote_op() -> None:
    profile = _vault_with(Skill(name="Kubernetes", status="confirmed"))

    ops = demote_ops_for_denials(profile, ["Kubernetes"])

    assert [(o.name, o.declared_denial) for o in ops] == [("Kubernetes", "Kubernetes")]


def test_containment_only_retraction_never_demotes() -> None:
    """A retraction of "Tailwind CSS" is no statement whatsoever about bare
    "CSS" — asserting one would fabricate testimony (ADR-059 amended
    2026-08-08, the assert/refuse split of #486)."""
    profile = _vault_with(Skill(name="CSS", status="confirmed"))

    assert demote_ops_for_denials(profile, ["Tailwind CSS"]) == []


def test_a_broader_declared_term_does_demote_the_narrower_vault_entry() -> None:
    """The declared branch is directional: "CSS" declares "Tailwind CSS"."""
    profile = _vault_with(Skill(name="Tailwind CSS", status="confirmed"))

    ops = demote_ops_for_denials(profile, ["CSS"])

    assert [o.name for o in ops] == ["Tailwind CSS"]


def test_the_longest_declared_match_is_the_recorded_one() -> None:
    profile = _vault_with(Skill(name="Microsoft Azure DevOps", status="confirmed"))

    ops = demote_ops_for_denials(profile, ["Azure", "Microsoft Azure"])

    assert [o.declared_denial for o in ops] == ["Microsoft Azure"]


def test_only_confirmed_skills_are_demoted() -> None:
    """#485's decided scope: the retraction of a skill the vault holds as
    `confirmed`. An `unconfirmed` entry is the reconciler's own inference and
    already backs nothing (ADR-061 clause 3)."""
    profile = _vault_with(Skill(name="Kubernetes", status="unconfirmed"))

    assert demote_ops_for_denials(profile, ["Kubernetes"]) == []


def test_certifications_and_languages_are_out_of_scope_for_demotion() -> None:
    profile = MasterProfileData(
        certifications=[Certification(name="Kubernetes", status="confirmed")],
        languages=[Language(language="Kubernetes", status="confirmed")],
    )

    assert demote_ops_for_denials(profile, ["Kubernetes"]) == []


@pytest.mark.asyncio
async def test_the_shared_reconcile_core_emits_the_demote_op() -> None:
    """ADR-066 — the emission rule lives in the core, not per door: every
    caller of `reconcile` gets the op in `result.ops` and hands it to
    `apply_ops` unchanged."""
    profile = _vault_with(Skill(name="Kubernetes", status="confirmed"))
    provider = _StubProvider(
        {"ops": [], "ambiguities": [], "denials": ["Kubernetes"]}
    )

    result = await reconcile(
        profile, {"answer": "I have never touched Kubernetes."}, SOURCE, provider
    )

    assert [type(o) for o in result.ops] == [DemoteSkill]
    applied = apply_ops(profile, result.ops, SOURCE)
    assert applied.profile.skills[0].status == "denied"


@pytest.mark.asyncio
async def test_the_shared_reconcile_core_does_not_demote_by_containment() -> None:
    profile = _vault_with(Skill(name="CSS", status="confirmed"))
    provider = _StubProvider(
        {"ops": [], "ambiguities": [], "denials": ["Tailwind CSS"]}
    )

    result = await reconcile(
        profile, {"answer": "Tailwind CSS I have never used."}, SOURCE, provider
    )

    assert [o for o in result.ops if isinstance(o, DemoteSkill)] == []


# ── 3. Nothing leaves `denied` ───────────────────────────────────────────────


def test_a_reimport_upsert_does_not_resurrect_a_denied_skill() -> None:
    """The near-dupe auto-merge site (`len(near) == 1`). A CV re-import naming
    the retracted skill is exactly the live vector the adversarial pass found."""
    profile = _vault_with(Skill(name="Kubernetes", status="denied"))

    result = apply_ops(
        profile, [UpsertSkill(name="Kubernetes", status="confirmed")], SOURCE
    )

    assert result.profile.skills[0].status == "denied"


def test_a_user_confirmed_merge_does_not_resurrect_a_denied_skill() -> None:
    """The second skill merge site (`user_confirmed == "merge"`)."""
    from applire.services.profile.reconcile.apply import _apply_upsert_skill

    profile = _vault_with(Skill(name="Kubernetes", status="denied"))
    changes: list[Any] = []

    _apply_upsert_skill(
        UpsertSkill(name="Kubernetes", status="confirmed"),
        profile,
        lambda h: None,
        changes,
        [],
        user_confirmed="merge",
    )

    assert profile.skills[0].status == "denied"


def test_the_promote_guard_refuses_denied_for_every_merge_site() -> None:
    """The certification and language merge sites carry the identical shape.
    `denied` is not in their status Literal today (#485 scopes the taxonomy
    change to skills), so they are pinned at the shared helper all four sites
    call — the guard, not four copies of it."""
    from applire.services.profile.reconcile.apply import _promote_to_confirmed

    denied = Skill(name="Kubernetes", status="denied")
    assert _promote_to_confirmed(denied, "confirmed") is False
    assert denied.status == "denied"

    unconfirmed = Skill(name="Terraform", status="unconfirmed")
    assert _promote_to_confirmed(unconfirmed, "confirmed") is True
    assert unconfirmed.status == "confirmed"


def test_certification_and_language_merges_still_promote_unconfirmed() -> None:
    """The guard must not over-fire: the ADR-061 clause 3 promotion it was
    written for keeps working at the other two sites."""
    profile = MasterProfileData(
        certifications=[Certification(name="ISO 9001", status="unconfirmed")],
        languages=[Language(language="German", status="unconfirmed")],
    )

    result = apply_ops(
        profile,
        [
            UpsertCertification(name="ISO 9001", status="confirmed"),
            UpsertLanguage(language="German", status="confirmed"),
        ],
        SOURCE,
    )

    assert result.profile.certifications[0].status == "confirmed"
    assert result.profile.languages[0].status == "confirmed"


def test_a_same_turn_upsert_cannot_outrank_the_turn_s_own_demotion() -> None:
    """Op order: the demote op is appended last, so a turn that both mentions
    and retracts a skill ends `denied`."""
    profile = _vault_with(Skill(name="Kubernetes", status="denied"))

    result = apply_ops(
        profile,
        [
            UpsertSkill(name="Kubernetes", status="confirmed"),
            DemoteSkill(name="Kubernetes"),
        ],
        SOURCE,
    )

    assert result.profile.skills[0].status == "denied"


# ── 4. Every claim surface excludes `denied` ─────────────────────────────────


def test_the_shared_predicate_excludes_denied_entries() -> None:
    filtered = exclude_unconfirmed(
        {
            "skills": [
                {"name": "Kubernetes", "status": "denied"},
                {"name": "Python", "status": "confirmed"},
                {"name": "Rust", "status": "unconfirmed"},
            ]
        }
    )

    assert [s["name"] for s in filtered["skills"]] == ["Python"]


def test_a_denied_skill_cannot_release_a_persisted_denial() -> None:
    """Composition with #480 step 1: the affirmation corpus is built from the
    CONFIRMED vault, so a demoted entry never becomes the independent
    affirmation that releases a containment-matched denial."""
    from applire.services.keyword_ledger import profile_literal_corpus
    from applire.services.profile.reconcile.stance import is_denied_concept

    vault = {"skills": [{"name": "CSS", "status": "denied"}]}
    corpus = profile_literal_corpus(exclude_unconfirmed(vault))

    assert is_denied_concept("CSS", ["Tailwind CSS"], corpus) is True


def test_a_denied_skill_does_not_back_a_claimable_ledger_row() -> None:
    from applire.services.keyword_ledger import assert_claimable_backed

    ledger = [
        {
            "concept": "Kubernetes",
            "status": "direct",
            "claimable": True,
            "evidence": "Ran the cluster migration",
        }
    ]
    healed, violations = assert_claimable_backed(
        ledger,
        {"skills": [{"name": "Kubernetes", "status": "denied"}]},
        seam="test",
    )

    assert [v["reason"] for v in violations] == ["no_vault_evidence_unit"]
    assert healed[0]["claimable"] is False


# ── Both doors, through the shared path ──────────────────────────────────────


async def _seed_vault_with_confirmed_kubernetes(db):
    from tests.support.profile_factory import make_master_profile

    record = make_master_profile(
        profile_json={
            "personal_info": {"full_name": "Anna Bauer"},
            "skills": [
                {"name": "Kubernetes", "category": "technical", "status": "confirmed"},
                {"name": "CSS", "category": "technical", "status": "confirmed"},
            ],
            "metadata": {
                "completeness_score": 0.5,
                "created_via": "cv_upload",
                "created_at": "2026-01-01T00:00:00Z",
                "last_updated": "2026-01-01T00:00:00Z",
            },
        }
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


_RETRACTION_PAYLOAD = {
    "ops": [],
    "ambiguities": [],
    "denials": ["Kubernetes"],
}


@pytest.mark.asyncio
async def test_testimony_door_demotes_the_retracted_skill(async_db) -> None:
    from applire.services.profile.reconcile.testimony_bridge import submit_testimony

    record = await _seed_vault_with_confirmed_kubernetes(async_db)

    await submit_testimony(
        "Scratch that — I have never actually worked with Kubernetes.",
        async_db,
        _StubProvider(_RETRACTION_PAYLOAD),
    )
    await async_db.refresh(record)

    by_name = {s["name"]: s for s in record.profile_json["skills"]}
    assert by_name["Kubernetes"]["status"] == "denied"
    # Mark, don't delete — and the untouched sibling is untouched.
    assert by_name["Kubernetes"]["category"] == "technical"
    assert by_name["CSS"]["status"] == "confirmed"

    receipts = [
        c
        for rec in record.profile_json["metadata"]["enrichment_history"]
        for c in rec["changes"]
        if c["section"] == "skills" and c["field"] == "status"
    ]
    assert [(c["old_value"], c["new_value"]) for c in receipts] == [
        ("confirmed", "denied")
    ]


@pytest.mark.asyncio
async def test_agent_door_demotes_the_retracted_skill(async_db) -> None:
    from applire.schemas.claims import ClaimItem, ClaimsSubmission
    from applire.services.profile.reconcile.agent_bridge import submit_agent_claims

    record = await _seed_vault_with_confirmed_kubernetes(async_db)

    await submit_agent_claims(
        ClaimsSubmission(
            claims=[
                ClaimItem(
                    statement=(
                        "Correction: I have never actually worked with Kubernetes."
                    )
                )
            ]
        ),
        None,
        async_db,
        _StubProvider(_RETRACTION_PAYLOAD),
    )
    await async_db.refresh(record)

    by_name = {s["name"]: s for s in record.profile_json["skills"]}
    assert by_name["Kubernetes"]["status"] == "denied"
    assert by_name["CSS"]["status"] == "confirmed"

    receipts = [
        c
        for rec in record.profile_json["metadata"]["enrichment_history"]
        for c in rec["changes"]
        if c["section"] == "skills" and c["field"] == "status"
    ]
    assert [(c["old_value"], c["new_value"]) for c in receipts] == [
        ("confirmed", "denied")
    ]


@pytest.mark.asyncio
async def test_interview_door_demotes_the_retracted_skill() -> None:
    from applire.services.profile.reconcile.interview_bridge import (
        reconcile_interview_turn,
    )

    # #480 PR 2 — the bridge writes through `commit_ops`, so it takes the
    # session and the row it is writing.
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from applire.db.session import Base
    from applire.models.profile import MasterProfile, authorized_profile_write

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=[MasterProfile.__table__])
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        with authorized_profile_write():
            record = MasterProfile(
                profile_json={"skills": [{"name": "Kubernetes", "status": "confirmed"}]}
            )
        db.add(record)
        await db.commit()

        turn = await reconcile_interview_turn(
            db,
            profile_record=record,
            gap="Kubernetes",
            question="How much Kubernetes have you run in production?",
            answer="None — I have never actually worked with Kubernetes.",
            provider=_StubProvider(_RETRACTION_PAYLOAD),
            session_id="s1",
        )
        await db.commit()
    await engine.dispose()

    assert turn.profile_dict["skills"][0]["status"] == "denied"
    # F8 — a retraction is not a resolved gap, however much it changed the
    # vault. `addressed` drives the ledger upgrade and gap-advance in
    # session.py, and it must stay False for a turn whose only vault effect is
    # the candidate taking something back.
    assert turn.addressed is False
    assert turn.denial_recorded is True
    # ...and the demotion is still receipted (ADR-059 clause 1).
    assert any(
        c.section == "skills" and c.field == "status" and c.new_value == "denied"
        for c in turn.changes
    )


# ── The cv.py claim surfaces (ADR-061 amendment clause 2 consolidation) ──────


def _tailored(skills: list[str]):
    from applire.schemas.cv import TailoredContact, TailoredCVData

    return TailoredCVData(
        contact=TailoredContact(name="Anna Bauer"), skills=list(skills)
    )


def test_certifications_passthrough_excludes_denied() -> None:
    from applire.services.cv import _apply_certifications

    out = _apply_certifications(
        _tailored([]),
        {
            "certifications": [
                {"name": "CKA", "status": "denied"},
                {"name": "ISO 9001", "status": "confirmed"},
            ]
        },
    )

    assert [c.name for c in out.certifications] == ["ISO 9001"]


def test_jd_skill_guarantee_never_restores_a_denied_skill() -> None:
    """`_tailor_skills_to_jd`'s #192 guarantee re-adds JD-required skills the
    writer dropped — from the vault's own spelling. A retracted skill is not a
    restoration candidate."""
    from applire.services.cv import _tailor_skills_to_jd

    out = _tailor_skills_to_jd(
        _tailored(["Python"]),
        {"skills": [{"name": "Kubernetes", "status": "denied"}]},
        {"required_skills": ["Kubernetes"], "nice_to_have_skills": []},
        None,
        cap=20,
    )

    assert "Kubernetes" not in (out.skills or [])


def test_jd_echo_drop_gives_a_denied_skill_no_vault_tie() -> None:
    from applire.services.cv import _drop_ungrounded_jd_echo_skills

    out = _drop_ungrounded_jd_echo_skills(
        _tailored(["Kubernetes"]),
        {"skills": [{"name": "Kubernetes", "status": "denied"}]},
        {"required_skills": ["Kubernetes"], "nice_to_have_skills": []},
        None,
    )

    assert "Kubernetes" not in (out.skills or [])


def test_spelling_restore_never_targets_a_denied_skill() -> None:
    from applire.services.cv import _restore_skill_spelling

    out = _restore_skill_spelling(
        _tailored(["kubernetes"]),
        {"skills": [{"name": "Kubernetes", "status": "denied"}]},
    )

    assert out.skills == ["kubernetes"]


def test_narrative_named_skill_restore_never_adds_a_denied_skill() -> None:
    """#376's skills-list gap guard adds a name the narrative already spells
    out. A retracted skill named in a surviving bullet must not be put back on
    the page — neither via the vault pool nor via a claimable ledger row it
    could still ground against."""
    from applire.schemas.cv import TailoredContact, TailoredCVData, TailoredWorkEntry
    from applire.services.cv import _restore_narrative_named_skills

    tailored = TailoredCVData(
        contact=TailoredContact(name="Anna Bauer"),
        skills=[],
        work_history=[
            TailoredWorkEntry(
                id="w1",
                company="ACME",
                position="SRE",
                start_date="2020-01",
                end_date="2024-01",
                bullets=["Migrated the platform onto Kubernetes"],
            )
        ],
    )

    out = _restore_narrative_named_skills(
        tailored,
        {"skills": [{"name": "Kubernetes", "status": "denied"}]},
        [
            {
                "concept": "Kubernetes",
                "status": "direct",
                "claimable": True,
                "evidence": "ran the cluster",
                "surface_forms": ["Kubernetes"],
            }
        ],
    )

    assert "Kubernetes" not in (out.skills or [])


def test_narrative_named_skill_restore_still_adds_a_confirmed_skill() -> None:
    """The guard must not over-fire: #376's actual job still works."""
    from applire.schemas.cv import TailoredContact, TailoredCVData, TailoredWorkEntry
    from applire.services.cv import _restore_narrative_named_skills

    tailored = TailoredCVData(
        contact=TailoredContact(name="Anna Bauer"),
        skills=[],
        work_history=[
            TailoredWorkEntry(
                id="w1",
                company="ACME",
                position="SRE",
                start_date="2020-01",
                end_date="2024-01",
                bullets=["Migrated the platform onto Kubernetes"],
            )
        ],
    )

    out = _restore_narrative_named_skills(
        tailored,
        {
            "skills": [
                {"name": "Kubernetes", "status": "confirmed", "experience_refs": ["w1"]}
            ],
            "work_experience": [
                {
                    "id": "w1",
                    "company": "ACME",
                    "position": "SRE",
                    "technologies": ["Kubernetes"],
                }
            ],
        },
        None,
    )

    assert "Kubernetes" in (out.skills or [])


def test_cv_py_has_no_ad_hoc_unconfirmed_status_filters_left() -> None:
    """ADR-061 amendment clause 2: "one place" is made true, not assumed. The
    five helpers that open-coded the status literal now go through the shared
    predicate — this fails the moment a sixth copy is added."""
    import inspect

    import applire.services.cv as cv_module

    source = inspect.getsource(cv_module)
    assert '"unconfirmed"' not in source


def test_a_cv_section_edit_naming_the_denied_skill_does_not_resurrect_it() -> None:
    """The other write path that can name an existing skill (#336's tailored-CV
    skills-box edit). It appends only names the vault does not already hold, so
    a retracted entry is left at `denied` rather than re-added as confirmed."""
    from applire.services.cv_section_editor import build_section_field_edit

    profile = _vault_with(Skill(name="Kubernetes", status="denied"))

    field, value = build_section_field_edit(
        "skills",
        "Kubernetes",
        profile_data=profile,
        content_snapshot=None,
        lang="en",
    )

    assert field == "skills"
    assert [(s["name"], s["status"]) for s in value] == [("Kubernetes", "denied")]
