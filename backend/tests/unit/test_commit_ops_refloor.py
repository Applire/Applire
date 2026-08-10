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

"""#480 PR 4 — invariant 2: the persisted-denial re-floor at the write seam.

ADR-059 amended 2026-08-08 step 2, home ADR-063 clause 8(d). Before this, the
floor only ever saw the denials the CURRENT turn declared, so a later write
through any door could re-introduce a skill the candidate had retracted and
nothing caught it until the next ledger rebuild — if one ever ran.

The pass sits inside `commit_ops`, **after `apply_ops` returns and before the
assignment**, and reads `metadata.denied_concepts` off the POST-op profile so a
denial recorded by the same turn is already in scope.

**One instrument, not a second one** (design §3.2): `demote_ops_for_denials` is
parameter-shaped and stays the single emission rule; the only change is that
this caller feeds it the PERSISTED denial list instead of the same-turn one.
Its `confirmed`-skills-only scope is by design (#485/#504) and is inherited.

**The never-upgrade half stays read-side** (§3.3): the re-floor writes `denied`
on a DECLARED match only. A compound denial never demotes a component skill —
that would fabricate testimony the candidate never gave (#486).

**Release: none** (§3.4). `commit_ops` never deletes a `DeniedConcept`, never
un-demotes and never consults an affirmation predicate to release. The seam is
reserved with a visible `NotImplementedError`; the un-denial act is #506.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.profile import (
    MasterProfile,
    ProfileSnapshot,
)
from applire.services.profile.commit import (
    UN_DENIAL_INTAKE,
    CommitProvenance,
    TurnGrounding,
    commit_ops,
)
from applire.services.profile.reconcile.ops import ReplaceSection, UpsertSkill

_SEED = {
    "personal_info": {"full_name": "Daniel Kovač", "email": "daniel@example.invalid"},
    "work_experience": [
        {
            "id": "w1",
            "company": "Rheinwerk GmbH",
            "role": "Automation Engineer",
            "start_date": "2018-01",
            "end_date": "2023-12",
        }
    ],
    "skills": [{"name": "Terraform", "category": "technical", "status": "confirmed"}],
    "metadata": {
        "completeness_score": 0.0,
        "created_via": "cv_upload",
        "created_at": "2020-01-01T00:00:00Z",
        "last_updated": "2020-01-01T00:00:00Z",
        "denied_concepts": [
            {
                "concept": "Ansible",
                "statement": "I have never used Ansible.",
                "source": "interview",
                "date": "2026-08-01",
                "denial_level": "direct",
            }
        ],
    },
}


def _prov(intake: str = "field_edit") -> CommitProvenance:
    """`source` follows the intake: a grounded testimony turn records denials,
    and `DeniedConcept.source` is a closed literal that `manual_edit` is not in."""
    source = "testimony" if intake == "testimony" else "manual_edit"
    return CommitProvenance(
        source=source, intake=intake, session_id=str(uuid.uuid4())
    )


@pytest_asyncio.fixture
async def db_session():
    from applire.db.session import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c, tables=[MasterProfile.__table__, ProfileSnapshot.__table__]
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded(db_session):
    import copy

    from applire.models.profile import authorized_profile_write

    with authorized_profile_write():
        record = MasterProfile(profile_json=copy.deepcopy(_SEED))
    db_session.add(record)
    await db_session.commit()
    return record


def _skill(record, name):
    return next(s for s in record.profile_json["skills"] if s["name"] == name)


# ── The invariant ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_write_re_introducing_a_denied_skill_is_refloored(db_session, seeded):
    """THE durable invariant. The denial is already persisted; the write adds
    the skill back as `confirmed`, and the seam takes it straight back."""
    result = await commit_ops(
        db_session,
        [UpsertSkill(name="Ansible", category="technical", status="confirmed")],
        _prov(),
    )

    assert result.refloored, "the re-introduced skill must be taken back"
    assert _skill(seeded, "Ansible")["status"] == "denied"


@pytest.mark.asyncio
async def test_the_refloor_is_receipted_but_is_never_a_change(db_session, seeded):
    """Invariant 7's separation, extended to row 2: a re-flooring is a
    retraction, so it may never read as gap-addressing content (#231/#352)."""
    result = await commit_ops(
        db_session,
        [UpsertSkill(name="Ansible", category="technical", status="confirmed")],
        _prov(),
    )

    receipt = seeded.profile_json["metadata"]["enrichment_history"][0]["changes"]
    assert result.refloored
    assert len(receipt) == len(result.changes) + len(result.refloored) + len(
        result.denials
    ) + len(result.demotions)
    assert not any(c in result.changes for c in result.refloored)


@pytest.mark.asyncio
async def test_bool_changes_is_unaffected_by_a_refloor(db_session, seeded):
    """A turn whose ONLY effect was a re-flooring must not read as `addressed`
    to the caller that asks `bool(result.changes)`."""
    result = await commit_ops(
        db_session,
        [ReplaceSection(
            section="skills",
            value=[
                {"name": "Terraform", "category": "technical", "status": "confirmed"},
                {"name": "Ansible", "category": "technical", "status": "confirmed"},
            ],
        )],
        _prov(),
    )

    assert result.refloored
    assert not any(
        c.section == "skills" and c.field == "status" and c.new_value == "denied"
        for c in result.changes
    )


@pytest.mark.asyncio
async def test_the_refloor_reads_the_persisted_list_not_only_this_turns(
    db_session, seeded
):
    """The input swap IS step 2: the denial was recorded in an EARLIER turn and
    this turn declares nothing at all, yet the floor still holds."""
    result = await commit_ops(
        db_session,
        [UpsertSkill(name="Ansible", category="technical", status="confirmed")],
        _prov(),
        grounding=TurnGrounding(text="Ansible is one of my strengths.", denials=[]),
    )

    assert result.refloored
    assert _skill(seeded, "Ansible")["status"] == "denied"


@pytest.mark.asyncio
async def test_a_same_turn_denial_is_already_in_scope(db_session, seeded):
    """Placement: the pass runs AFTER `record_denials`, so a denial this very
    turn recorded floors a skill this very turn wrote."""
    result = await commit_ops(
        db_session,
        [UpsertSkill(name="Puppet", category="technical", status="confirmed")],
        _prov("testimony"),
        grounding=TurnGrounding(
            text="I have never used Puppet.", denials=["Puppet"]
        ),
    )

    assert result.refloored or result.demotions
    assert _skill(seeded, "Puppet")["status"] == "denied"


@pytest.mark.asyncio
async def test_an_untouched_skill_is_left_alone(db_session, seeded):
    result = await commit_ops(db_session, [], _prov())

    assert result.refloored == []
    assert _skill(seeded, "Terraform")["status"] == "confirmed"


@pytest.mark.asyncio
async def test_the_refloor_is_idempotent_across_writes(db_session, seeded):
    """A re-flooring already applied produces no second receipt — a repeated
    save must not litter the enrichment history."""
    await commit_ops(
        db_session,
        [UpsertSkill(name="Ansible", category="technical", status="confirmed")],
        _prov(),
    )
    second = await commit_ops(db_session, [], _prov())

    assert second.refloored == []


# ── §3.3 — the never-upgrade half stays READ-side ────────────────────────────


@pytest.mark.asyncio
async def test_a_compound_denial_never_demotes_a_component_skill(db_session, seeded):
    """The canonical #486 example, at the write seam: a persisted denial of
    "Tailwind CSS" is no statement whatsoever about the vault's bare "CSS".
    The re-floor asserts on DECLARED matches only; the containment half stays
    read-side, where it refuses a CLAIM without writing testimony."""
    await commit_ops(
        db_session,
        [UpsertSkill(name="CSS", category="technical", status="confirmed")],
        _prov("testimony"),
        grounding=TurnGrounding(
            text="I have never used Tailwind CSS.", denials=["Tailwind CSS"]
        ),
    )
    result = await commit_ops(db_session, [], _prov())

    assert result.refloored == []
    assert _skill(seeded, "CSS")["status"] == "confirmed"


@pytest.mark.asyncio
async def test_an_unconfirmed_skill_is_not_demoted_by_the_refloor(db_session, seeded):
    """`demote_ops_for_denials`'s scope is inherited, not re-decided: an
    `unconfirmed` entry is the reconciler's own inference and already backs
    nothing (ADR-061 clause 3)."""
    result = await commit_ops(
        db_session,
        [UpsertSkill(name="Ansible", category="technical", status="unconfirmed")],
        _prov(),
    )

    assert result.refloored == []
    assert _skill(seeded, "Ansible")["status"] == "unconfirmed"


# ── §3.4 — the reserved UN_DENIAL seam ───────────────────────────────────────


@pytest.mark.asyncio
async def test_an_un_denial_intake_raises_rather_than_pretending(db_session, seeded):
    with pytest.raises(NotImplementedError) as exc:
        await commit_ops(db_session, [], _prov(UN_DENIAL_INTAKE))

    assert "506" in str(exc.value)


@pytest.mark.asyncio
async def test_the_un_denial_seam_writes_nothing(db_session, seeded):
    before = dict(seeded.profile_json)
    with pytest.raises(NotImplementedError):
        await commit_ops(
            db_session,
            [UpsertSkill(name="Ansible", category="technical", status="confirmed")],
            _prov(UN_DENIAL_INTAKE),
        )

    assert seeded.profile_json == before


@pytest.mark.asyncio
async def test_no_ordinary_commit_ever_deletes_a_persisted_denial(db_session, seeded):
    """Release: none. Whatever a turn affirms, the `DeniedConcept` survives."""
    await commit_ops(
        db_session,
        [UpsertSkill(name="Ansible", category="technical", status="confirmed")],
        _prov(),
        grounding=TurnGrounding(
            text="Actually I use Ansible daily now.", denials=[]
        ),
    )

    concepts = [
        d["concept"] for d in seeded.profile_json["metadata"]["denied_concepts"]
    ]
    assert "Ansible" in concepts
