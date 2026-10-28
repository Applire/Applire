# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 5 (U-11 / U-9) — merge two entries of one section, keep two
entries separate, and the import-time identity verdicts."""
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.entry_merge import EntryMergeRequest, KeepSeparateRequest  # noqa: E402
from applire.schemas.profile import (  # noqa: E402
    Conflict,
    EducationEntry,
    EntryPair,
    MasterProfileData,
    PendingConfirmation,
    ProfileMetadata,
    ProjectEntry,
    SignatureStory,
    Skill,
    VolunteerActivity,
    WorkEntry,
)
from applire.services.profile.reconcile.ops import (  # noqa: E402
    DecisionOp,
    MergeEntries,
    ReconcileOp,
)

from tests.support.profile_factory import make_master_profile  # noqa: E402


class StubProvider:
    """aparse_json returns a canned duplicates answer; counts calls."""

    def __init__(self, pairs=None, raises=False):
        self.pairs = pairs or []
        self.raises = raises
        self.calls = 0

    async def aparse_json(self, *a, **k):
        self.calls += 1
        if self.raises:
            raise RuntimeError("model down")
        return {"pairs": self.pairs}


def _vault() -> MasterProfileData:
    return MasterProfileData(
        work_experience=[
            WorkEntry(
                id="wa", company="Nordwind Logistik GmbH", role="Teamleiter Disposition",
                start_date="2019-03", end_date="2022-08", location=None,
                responsibilities=["Led a team of 8 dispatchers", "Planned weekly routes"],
                achievements=["Cut delivery delays by 20%"],
                technologies=["SAP", "Excel"],
            ),
            WorkEntry(
                id="wb", company="Nordwind Logistik", role="Team Lead Dispatch",
                start_date="2019-03", end_date="2022-08", location="Hamburg",
                responsibilities=["led a team of 8   dispatchers", "Introduced a shift handover checklist",
                                  "Managed the carrier relationships"],
                achievements=["Reduced late deliveries by a fifth"],
                technologies=["sap", "Power BI"],
            ),
            WorkEntry(id="wc", company="Other AG", role="Analyst", start_date="2015-01", end_date="2016-01"),
        ],
        skills=[Skill(id="s1", name="Dispatch planning", experience_refs=["wb", "wc"])],
        projects=[ProjectEntry(id="p1", name="Route tool", associated_experience="wb")],
        signature_stories=[SignatureStory(
            id="st1", title="T", challenge="c", mechanism="m", outcome="o", experience_refs=["wb", "wa"],
        )],
        metadata=ProfileMetadata(
            pending_conflicts=[Conflict(
                section="work_experience", field="role", entity_id="wb",
                existing_value="x", incoming_value="y", source="cv_upload",
            )],
        ),
    )


@pytest_asyncio.fixture
async def session():
    from applire.db.session import Base  # noqa: F401
    import applire.models  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _seed(session, data: MasterProfileData | None = None):
    record = make_master_profile(profile_json=(data or _vault()).model_dump(mode="json"))
    session.add(record)
    await session.commit()
    return record


def _req(mode="preview", section="work_experience", target="wa", source="wb", **choices):
    return EntryMergeRequest(
        section=section, target_id=target, source_id=source, mode=mode, choices=choices or None,
    )


def _pair(provider_pairs):
    return StubProvider(provider_pairs)


async def _preview(session, provider=None, **kw):
    from applire.services.profile.entry_merge import entry_merge

    return await entry_merge(session, _req("preview", **kw), provider or StubProvider())


async def _commit(session, **kw):
    from applire.services.profile.entry_merge import entry_merge

    return await entry_merge(session, _req("commit", **kw), StubProvider())


def _wa(response):
    return next(e for e in response.profile.work_experience if e.id == "wa")


# ── preview ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_preview_shape_aliases_exact_dupes_and_model_pairs(session):
    record = await _seed(session)
    before = dict(record.profile_json)
    # rest of source responsibilities after the exact dupe: [checklist, carriers];
    # the model pairs T2 ("Planned weekly routes") with S2 ("Managed the carrier relationships").
    provider = StubProvider([{"t": "T2", "s": "S2", "merged": "Planned weekly routes"}])
    prev = await _preview(session, provider)

    assert prev.merged_entry["id"] == "wa"
    assert prev.merged_entry["location"] == "Hamburg"  # A's empty scalar filled from B
    assert {(a.title, a.company) for a in prev.aliases} == {
        ("Team Lead Dispatch", None), (None, "Nordwind Logistik"),
    }
    assert prev.date_conflict is None
    ids = [p.pair_id for p in prev.duplicate_pairs]
    assert ids == ["responsibilities:1:2"]
    assert prev.duplicate_pairs[0].source_text == "Managed the carrier relationships"
    assert prev.duplicate_pairs[0].proposal == "Planned weekly routes"
    # exact dupes ("led a team of 8   dispatchers", "sap") are neither paired nor unique
    assert "Introduced a shift handover checklist" in prev.unique_bullets
    assert "Power BI" in prev.unique_bullets
    assert not any("dispatchers" in b for b in prev.unique_bullets)
    assert "sap" not in prev.unique_bullets
    assert provider.calls == 2  # one call per list with bullets on both sides (resp + ach)
    assert record.profile_json == before  # a preview writes nothing


@pytest.mark.asyncio
async def test_preview_reports_a_date_conflict(session):
    data = _vault()
    data.work_experience[1].start_date = "2019-05"
    await _seed(session, data)
    prev = await _preview(session)
    assert prev.date_conflict.target.start_date == "2019-03"
    assert prev.date_conflict.source.start_date == "2019-05"


@pytest.mark.asyncio
async def test_preview_education_has_no_bullet_pairs_and_aliases_degree(session):
    data = MasterProfileData(education=[
        EducationEntry(id="ea", institution="TU Dresden", degree="Diplom-Ingenieur"),
        EducationEntry(id="eb", institution="Technische Universität Dresden", degree="Dipl.-Ing.", grade="1,7"),
    ])
    await _seed(session, data)
    prev = await _preview(session, section="education", target="ea", source="eb")
    assert prev.duplicate_pairs == [] and prev.unique_bullets == []
    assert {(a.title, a.company) for a in prev.aliases} == {
        ("Dipl.-Ing.", None), (None, "Technische Universität Dresden"),
    }
    assert prev.merged_entry["grade"] == "1,7"


@pytest.mark.asyncio
async def test_validation_codes(session):
    from applire.routers.profile import merge_entries_endpoint

    await _seed(session)
    prov = StubProvider()
    with pytest.raises(HTTPException) as e:
        await merge_entries_endpoint(_req(target="wa", source="wa"), session, None, prov)
    assert e.value.status_code == 422
    with pytest.raises(HTTPException) as e:
        await merge_entries_endpoint(_req(target="wa", source="nope"), session, None, prov)
    assert e.value.status_code == 404
    with pytest.raises(Exception):  # 422 from the request model itself
        EntryMergeRequest(section="skills", target_id="a", source_id="b", mode="preview")


@pytest.mark.asyncio
async def test_no_profile_is_404(session):
    from applire.routers.profile import merge_entries_endpoint

    with pytest.raises(HTTPException) as e:
        await merge_entries_endpoint(_req(), session, None, StubProvider())
    assert e.value.status_code == 404


# ── commit: per outcome ───────────────────────────────────────────────────────


def _choice(outcome, merged=None):
    return {
        "pair_id": "responsibilities:1:2",
        "target_text": "Planned weekly routes",
        "source_text": "Managed the carrier relationships",
        "outcome": outcome,
        "merged_text": merged,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome,merged,expected_second,source_kept", [
    ("keep_current", None, "Planned weekly routes", False),
    ("take_new", None, "Managed the carrier relationships", False),
    ("keep_both", None, "Planned weekly routes", True),
    ("merge", "Planned weekly routes and the carrier relationships", "Planned weekly routes and the carrier relationships", False),
])
async def test_commit_duplicate_outcomes(session, outcome, merged, expected_second, source_kept):
    await _seed(session)
    resp = await _commit(session, duplicates=[_choice(outcome, merged)])
    wa = _wa(resp)
    assert wa.responsibilities[0] == "Led a team of 8 dispatchers"
    assert wa.responsibilities[1] == expected_second
    assert ("Managed the carrier relationships" in wa.responsibilities) is (
        source_kept or outcome == "take_new"
    )
    assert "Introduced a shift handover checklist" in wa.responsibilities  # unique bullet appended
    assert wa.responsibilities.count("Led a team of 8 dispatchers") == 1  # exact dupe merged
    assert wa.technologies == ["SAP", "Excel", "Power BI"]
    assert wa.location == "Hamburg"
    assert wa.role_aliases == ["Team Lead Dispatch"]
    assert wa.company_aliases == ["Nordwind Logistik"]
    assert [e.id for e in resp.profile.work_experience] == ["wa", "wc"]  # B removed
    # one history record names what was merged
    head = resp.profile.metadata.enrichment_history[-1]
    merged_change = next(c for c in head.changes if c.action == "merged")
    assert merged_change.old_value == "Nordwind Logistik / Team Lead Dispatch"


@pytest.mark.asyncio
async def test_an_unmentioned_pair_is_keep_both(session):
    await _seed(session)
    resp = await _commit(session)
    wa = _wa(resp)
    assert "Managed the carrier relationships" in wa.responsibilities
    assert "Planned weekly routes" in wa.responsibilities
    assert "Reduced late deliveries by a fifth" in wa.achievements


@pytest.mark.asyncio
async def test_an_unverifiable_merged_text_is_still_written_and_logged(session, caplog):
    await _seed(session)
    with caplog.at_level("INFO"):
        resp = await _commit(session, duplicates=[_choice("merge", "Completely invented Zürich achievement 99%")])
    assert "Completely invented Zürich achievement 99%" in _wa(resp).responsibilities
    assert any("not verifiable" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_stale_preview_is_409(session):
    from applire.routers.profile import merge_entries_endpoint

    await _seed(session)
    bad = _choice("keep_both")
    bad["target_text"] = "A bullet the entry no longer holds"
    with pytest.raises(HTTPException) as e:
        await merge_entries_endpoint(_req("commit", duplicates=[bad]), session, None, StubProvider())
    assert (e.value.status_code, e.value.detail) == (409, "stale_preview")
    bad = _choice("keep_both")
    bad["source_text"] = "Not on the source"
    with pytest.raises(HTTPException) as e:
        await merge_entries_endpoint(_req("commit", duplicates=[bad]), session, None, StubProvider())
    assert e.value.detail == "stale_preview"


@pytest.mark.asyncio
async def test_dates_choice_required_and_both_sides(session):
    from applire.routers.profile import merge_entries_endpoint

    data = _vault()
    data.work_experience[1].start_date = "2019-05"
    data.work_experience[1].end_date = "2022-12"
    await _seed(session, data)
    with pytest.raises(HTTPException) as e:
        await merge_entries_endpoint(_req("commit"), session, None, StubProvider())
    assert (e.value.status_code, e.value.detail) == (422, "dates_choice_required")
    resp = await _commit(session, dates="source")
    assert (_wa(resp).start_date, _wa(resp).end_date) == ("2019-05", "2022-12")


@pytest.mark.asyncio
async def test_dates_choice_target_keeps_target(session):
    data = _vault()
    data.work_experience[1].start_date = "2019-05"
    await _seed(session, data)
    resp = await _commit(session, dates="target")
    assert _wa(resp).start_date == "2019-03"


# ── commit: every reference to B moves to A ───────────────────────────────────


@pytest.mark.asyncio
async def test_skill_experience_refs_move_to_the_target(session):
    await _seed(session)
    resp = await _commit(session)
    assert resp.profile.skills[0].experience_refs == ["wa", "wc"]


@pytest.mark.asyncio
async def test_project_parent_moves_to_the_target(session):
    await _seed(session)
    resp = await _commit(session)
    assert resp.profile.projects[0].associated_experience == "wa"


@pytest.mark.asyncio
async def test_story_evidence_moves_to_the_target_without_duplicates(session):
    await _seed(session)
    resp = await _commit(session)
    assert resp.profile.signature_stories[0].experience_refs == ["wa"]


@pytest.mark.asyncio
async def test_pending_conflict_entity_id_moves_to_the_target(session):
    await _seed(session)
    resp = await _commit(session)
    assert resp.profile.metadata.pending_conflicts[0].entity_id == "wa"


@pytest.mark.asyncio
async def test_confirmation_context_and_pair_records_move(session):
    data = _vault()
    data.metadata.pending_confirmations = [PendingConfirmation(
        question="q", options=["a"], context={"work_entry_id": "wb"},
    )]
    data.metadata.separate_entry_pairs = [
        EntryPair(section="work_experience", ids=["wa", "wb"]),
        EntryPair(section="work_experience", ids=["wb", "wc"]),
    ]
    await _seed(session, data)
    resp = await _commit(session)
    meta = resp.profile.metadata
    assert meta.pending_confirmations[0].context["work_entry_id"] == "wa"
    assert [p.ids for p in meta.separate_entry_pairs] == [["wa", "wc"]]


@pytest.mark.asyncio
async def test_volunteer_and_project_merge_store_what_the_schema_can(session):
    data = MasterProfileData(
        volunteer_activities=[
            VolunteerActivity(id="va", organization="Tafel Hamburg e.V.", role="Helfer"),
            VolunteerActivity(id="vb", organization="Hamburger Tafel", role="Koordinator", cause="Social"),
        ],
        projects=[ProjectEntry(id="pa", name="Route tool"), ProjectEntry(id="pb", name="Routing-Werkzeug", url="https://x.example")],
    )
    await _seed(session, data)
    resp = await _commit(session, section="volunteer_activities", target="va", source="vb")
    v = resp.profile.volunteer_activities
    assert [x.id for x in v] == ["va"]
    assert v[0].organization_aliases == ["Hamburger Tafel"] and v[0].cause == "Social"
    resp = await _commit(session, section="projects", target="pa", source="pb")
    assert [p.id for p in resp.profile.projects] == ["pa"] and resp.profile.projects[0].url == "https://x.example"


# ── undo, op boundary ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_undo_last_merge_restores_both_entries(session):
    from applire.services.profile.snapshots import undo_last_merge

    record = await _seed(session)
    await _commit(session)
    undone = await undo_last_merge(session)
    assert undone.restored is True and undone.discarded_later_edits is False
    await session.refresh(record)
    restored = MasterProfileData.model_validate(record.profile_json)
    assert [e.id for e in restored.work_experience] == ["wa", "wb", "wc"]
    assert restored.skills[0].experience_refs == ["wb", "wc"]


def test_the_new_ops_are_adapter_only():
    from pydantic import TypeAdapter, ValidationError

    for op in ("merge_entries", "keep_entries_separate", "record_entry_identity_verdicts"):
        with pytest.raises(ValidationError):
            TypeAdapter(ReconcileOp).validate_python({"op": op})
    TypeAdapter(DecisionOp).validate_python(
        {"op": "merge_entries", "section": "projects", "target_id": "a", "source_id": "b"}
    )
    assert MergeEntries.model_fields["op"].default == "merge_entries"


# ── keep separate ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_keep_separate_is_idempotent_and_404s(session):
    from applire.routers.profile import keep_entries_separate_endpoint

    record = await _seed(session)
    body = KeepSeparateRequest(section="work_experience", entry_ids=["wb", "wa"])
    await keep_entries_separate_endpoint(body, session, None)
    resp = await keep_entries_separate_endpoint(body, session, None)
    pairs = resp.profile.metadata.separate_entry_pairs
    assert len(pairs) == 1 and pairs[0].ids == ["wa", "wb"]
    assert [e.id for e in resp.profile.work_experience] == ["wa", "wb", "wc"]  # nothing merged
    with pytest.raises(HTTPException) as e:
        await keep_entries_separate_endpoint(
            KeepSeparateRequest(section="work_experience", entry_ids=["wa", "zz"]), session, None
        )
    assert e.value.status_code == 404


# ── U-9: verdicts at import ───────────────────────────────────────────────────


def _same_dates_cv() -> MasterProfileData:
    return MasterProfileData(work_experience=[
        WorkEntry(id="i1", company="Acme GmbH", role="Engineer", start_date="2020-01", end_date="2021-01"),
        WorkEntry(id="i2", company="ACME", role="Software Engineer", start_date="2020-01", end_date="2021-01"),
    ])


class JudgeProvider:
    def __init__(self, raises=False):
        self.raises = raises
        self.prompts = []

    async def aparse_json(self, prompt, **kw):
        self.prompts.append(prompt)
        if self.raises:
            raise RuntimeError("down")
        return {"verdicts": [{"pair_id": "work_experience:i1:i2", "verdict": "same"}]}


async def _import(session, provider):
    from applire.services.profile import _apply_merge

    return await _apply_merge(
        session, _same_dates_cv(), source="cv_upload", emb_provider=None, provider=provider,
    )


@pytest.mark.asyncio
async def test_identity_verdicts_are_persisted_at_import(session):
    from sqlalchemy import select

    from applire.models.profile import MasterProfile

    provider = JudgeProvider()
    await _import(session, provider)
    assert len(provider.prompts) == 1  # ONE call over every pair
    row = (await session.execute(select(MasterProfile))).scalar_one()
    meta = MasterProfileData.model_validate(row.profile_json).metadata
    assert [(v.section, v.ids, v.verdict) for v in meta.entry_identity_verdicts] == [
        ("work_experience", ["i1", "i2"], "same"),
    ]
    assert len(meta.enrichment_history) == 1  # same record as the import


@pytest.mark.asyncio
async def test_an_import_survives_a_judge_that_raises(session, monkeypatch):
    from sqlalchemy import select

    from applire.models.profile import MasterProfile
    from applire.services.profile import decisions

    await _import(session, JudgeProvider(raises=True))  # judge_identity swallows
    async def boom(*a, **k):
        raise RuntimeError("judge exploded")

    monkeypatch.setattr(decisions, "judge_identity", boom)
    await session.execute(MasterProfile.__table__.delete())
    await session.commit()
    await _import(session, JudgeProvider())
    row = (await session.execute(select(MasterProfile))).scalar_one()
    data = MasterProfileData.model_validate(row.profile_json)
    assert len(data.work_experience) == 2 and data.metadata.entry_identity_verdicts == []


def test_merge_keeps_the_sources_not_yet_reached_answers():
    """RULING B-1: a "not reached yet" answer on the merged-away entry moves to
    the target — without it the writers state the goal as a result again."""
    from applire.services.profile.reconcile.apply import _apply_merge_entries

    profile = _vault()
    target, source = profile.work_experience[0], profile.work_experience[1]
    target.not_yet_reached = ["Cut delivery delays by 20%"]
    source.not_yet_reached = ["Introduced a shift handover checklist", "Cut delivery delays by 20%"]
    _apply_merge_entries(
        MergeEntries(section="work_experience", target_id="wa", source_id="wb"), profile, [],
    )
    assert [e.id for e in profile.work_experience] == ["wa", "wc"]
    assert target.not_yet_reached == ["Cut delivery delays by 20%", "Introduced a shift handover checklist"]
