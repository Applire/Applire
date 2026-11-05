# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A-1 heal (Hazelnut build 6; founder RULING A-1 = B, A-2 = A, 2026-11-04).

Before G-3's forward fix, one clicked "…, aber zu X, Y und Z steht in meinem
Profil noch nichts" choice could persist X, Y and Z as the candidate's own
denials. The offered choice itself was never persisted, so the old rows are
found by their wording (phrase-coupled, RULING A-1): ONE Health-hub question per
affected statement, answered in place; "Ja, zurücknehmen" withdraws every
concept of the statement (G-5's act), "Doch, das fehlt mir" keeps them.

Pinned here:

1. the detector (``stance.profile_silence_clauses`` / ``offered_choice_denial_hits``):
   the clause family, grouping per statement, and the rows it must NOT offer —
   a typed denial, a concept also typed beside the chip, another door;
2. the builder's approved copy (RULING A-2) and the parker (never twice);
3. seams: the committer parks on an ordinary write; the Health hub answers it
   in place; both answers through ``resolve_confirmation``;
4. the 0086 data migration: its copied detector and builder agree with the
   app's, and it parks / unparks on a real SQLite database (Alembic
   ``Operations``), idempotently.

Synthetic data only.
"""

import copy
import importlib.util
import json
import uuid
from datetime import datetime
from pathlib import Path

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_REPO = Path(__file__).resolve().parents[2]
_MIGRATION = _REPO / "backend" / "alembic" / "versions" / "0086_offered_choice_denial_heal.py"

CHIP = (
    "Bei der Veltrup Diagnostik habe ich die Anbindung der Labor-LIMS an SAP S/4HANA verantwortet "
    "und die Schnittstellenstrategie sowie die Integrations-Governance festgelegt, aber zu Boomi, "
    "Kafka, AWS, iPaaS, ESB und Referenzarchitekturen steht in meinem Profil noch nichts."
)
CHIP_EN = (
    "At Veltrup Diagnostik I owned the connection of the lab LIMS to SAP S/4HANA, but my profile "
    "does not yet document Boomi, Kafka or reference architectures."
)
TYPED = "Mit Boomi habe ich nicht gearbeitet."
MIXED = CHIP + " Mit Kafka habe ich wirklich nie gearbeitet."
CLAUSE_TERMS = ["Boomi", "Kafka", "AWS", "iPaaS", "ESB", "Referenzarchitekturen"]


def _row(concept, statement, *, source="interview", date="2026-11-02"):
    return {
        "concept": concept, "statement": statement, "source": source, "date": date,
        "denial_level": "direct", "probe_asked": False,
    }


def _vault(rows, *, skills=None, pending=None, answered=None):
    return {
        "personal_info": {"full_name": "Lena Marquardt", "email": "lena@example.invalid"},
        "work_experience": [{
            "id": "w1", "company": "Veltrup Diagnostik GmbH", "role": "Leiterin Labor-IT",
            "start_date": "2019-04", "is_current": True,
        }],
        "skills": skills or [{"id": "s-py", "name": "Python", "category": "technical", "status": "confirmed"}],
        "metadata": {
            "completeness_score": 0.0, "created_via": "cv_upload",
            "created_at": "2020-01-01T00:00:00Z", "last_updated": "2020-01-01T00:00:00Z",
            "denied_concepts": copy.deepcopy(rows),
            "pending_confirmations": copy.deepcopy(pending or []),
            "denial_scope_answered": list(answered or []),
        },
    }


def _founder_shape_rows():
    """The founder's shape: every clause concept a row with the chip as statement,
    plus a typed denial (own statement) that must NOT be offered."""
    return [_row(t, CHIP) for t in CLAUSE_TERMS] + [_row("Boomi", TYPED, date="2026-11-03")]


# ---------------------------------------------------------------------------
# 1. The detector
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("statement", "must_contain"),
    [
        (CHIP, "zu boomi, kafka, aws, ipaas, esb und referenzarchitekturen steht in meinem profil noch nichts"),
        (CHIP_EN, "my profile does not yet document boomi, kafka or reference architectures"),
        ("Ich habe Power-BI-Dashboards gebaut; zu Self-Service-BI steht in meinem Profil bisher nichts.",
         "zu self service bi steht in meinem profil bisher nichts"),
        ("Ich kenne Verpackungslinien, aber weitere Verpackungsbranchen sind in meinem Profil noch nicht dokumentiert.",
         "weitere verpackungsbranchen sind in meinem profil noch nicht dokumentiert"),
        ("I ran the billing platform, but my profile doesn't cover PSD2 yet.", "my profile doesn't cover psd2 yet"),
    ],
)
def test_the_clause_family_is_found_and_cut_at_its_separator(statement, must_contain):
    from applire.services.profile.reconcile.stance import profile_silence_clauses

    clauses = profile_silence_clauses(statement)
    assert len(clauses) == 1 and clauses[0] == must_contain
    assert "veltrup" not in clauses[0] and "billing" not in clauses[0], "the affirmed half is not the clause"


@pytest.mark.parametrize(
    "statement",
    [
        TYPED,
        "I have not used FastAPI.",
        "Ich habe REST-APIs gebaut, aber mit FastAPI habe ich nicht gearbeitet.",
        "Mein Profil ist vollständig.",
    ],
)
def test_a_typed_no_has_no_profile_silence_clause(statement):
    from applire.services.profile.reconcile.stance import profile_silence_clauses

    assert profile_silence_clauses(statement) == []


def test_one_hit_per_statement_holding_every_clause_concept_and_no_typed_row():
    from applire.services.profile.reconcile.stance import offered_choice_denial_hits

    hits = offered_choice_denial_hits(_vault(_founder_shape_rows()))
    assert len(hits) == 1
    assert hits[0]["denial_concepts"] == CLAUSE_TERMS
    assert hits[0]["denial_date"] == "2026-11-02"
    assert len(hits[0]["statement_key"]) == 16


def test_a_concept_also_typed_beside_the_chip_is_never_offered():
    from applire.services.profile.reconcile.stance import offered_choice_denial_hits

    rows = [_row(t, MIXED) for t in CLAUSE_TERMS]
    (hit,) = offered_choice_denial_hits(_vault(rows))
    assert "Kafka" not in hit["denial_concepts"]
    assert hit["denial_concepts"] == [t for t in CLAUSE_TERMS if t != "Kafka"]


def test_a_concept_only_the_affirmed_half_names_is_not_offered():
    from applire.services.profile.reconcile.stance import offered_choice_denial_hits

    assert offered_choice_denial_hits(_vault([_row("SAP S/4HANA", CHIP)])) == []


@pytest.mark.parametrize("source", ["agent_interview", "testimony"])
def test_another_door_is_never_offered(source):
    """Only the built-in interview offered choices; an agent's or testimony's
    words are the claimant's own."""
    from applire.services.profile.reconcile.stance import offered_choice_denial_hits

    rows = [_row(t, CHIP, source=source) for t in CLAUSE_TERMS]
    assert offered_choice_denial_hits(_vault(rows)) == []


def test_two_statements_two_hits_and_the_earliest_date():
    from applire.services.profile.reconcile.stance import offered_choice_denial_hits

    rows = [_row("Boomi", CHIP, date="2026-11-03"), _row("Kafka", CHIP, date="2026-11-01"),
            _row("PSD2", "I ran billing, but my profile doesn't cover PSD2 yet.")]
    hits = offered_choice_denial_hits(_vault(rows))
    assert [h["denial_concepts"] for h in hits] == [["Boomi", "Kafka"], ["PSD2"]]
    assert hits[0]["denial_date"] == "2026-11-01"


@pytest.mark.parametrize(
    ("statement", "concepts", "expected"),
    [
        # adversarial H-2: the DE dative plural after "zu"
        ("Ich habe Arbeitsschutzunterweisungen mitgestaltet; zu ISO 45001 und "
         "Arbeitssicherheitsmanagementsystemen steht in meinem Profil noch nichts.",
         ["ISO 45001", "Arbeitssicherheitsmanagementsysteme"],
         ["ISO 45001", "Arbeitssicherheitsmanagementsysteme"]),
        # the reverse direction: the concept is plural, the clause singular
        ("Ich habe Schnittstellen gebaut, aber zu einer Referenzarchitektur steht in meinem Profil noch nichts.",
         ["Referenzarchitekturen"], ["Referenzarchitekturen"]),
        # the two wordings the adversarial pass added
        ("Ich habe REST-APIs gebaut, aber zu Kafka ist in meinem Profil noch nichts dokumentiert.",
         ["Kafka"], ["Kafka"]),
        ("I built REST APIs, but Kafka is not yet documented in my profile.", ["Kafka"], ["Kafka"]),
        # a short token stays exact: "AWS" is not found in "LAWS"
        ("Ich habe Verträge geprüft, aber zu LAWS steht in meinem Profil noch nichts.", ["AWS"], []),
    ],
)
def test_inflected_and_added_wordings_are_found(statement, concepts, expected):
    from applire.services.profile.reconcile.stance import offered_choice_denial_hits

    hits = offered_choice_denial_hits(_vault([_row(c, statement) for c in concepts]))
    assert sorted(c for h in hits for c in h["denial_concepts"]) == sorted(expected)


def test_a_denial_typed_as_a_continuation_of_the_clause_sentence_is_not_offered():
    """Adversarial H-3: the candidate removed the chip's full stop and went on."""
    from applire.services.profile.reconcile.stance import offered_choice_denial_hits

    s = CHIP.rstrip(".") + ", und mit Kafka habe ich tatsächlich nie gearbeitet."
    (hit,) = offered_choice_denial_hits(_vault([_row(t, s) for t in CLAUSE_TERMS]))
    assert "Kafka" not in hit["denial_concepts"] and len(hit["denial_concepts"]) == 5


def test_a_later_receipted_redenial_is_never_offered():
    from applire.services.profile.reconcile.stance import offered_choice_denial_hits

    vault = _vault(_founder_shape_rows())
    vault["metadata"]["enrichment_history"] = [{
        "id": "e1", "timestamp": "2026-11-05T09:00:00Z", "source": "interview",
        "changes": [{"section": "metadata", "field": "denied_concepts", "action": "updated",
                     "old_value": "Kafka", "new_value": "Kafka",
                     "rationale": "Re-confirmed limit: no hands-on Kafka (candidate's own testimony)"}],
    }]
    (hit,) = offered_choice_denial_hits(vault)
    assert "Kafka" not in hit["denial_concepts"] and len(hit["denial_concepts"]) == 5
    # An "added" receipt (the chip turn's own) excludes nothing.
    vault["metadata"]["enrichment_history"][0]["changes"][0]["action"] = "added"
    (hit,) = offered_choice_denial_hits(vault)
    assert hit["denial_concepts"] == CLAUSE_TERMS


@pytest.mark.parametrize("bad", [None, [], "x", {"metadata": None}, {"metadata": {"denied_concepts": ["x", None]}}])
def test_malformed_vaults_hit_nothing(bad):
    from applire.services.profile.reconcile.stance import offered_choice_denial_hits

    assert offered_choice_denial_hits(bad) == []


# ---------------------------------------------------------------------------
# 2. The approved copy and the parker
# ---------------------------------------------------------------------------


def test_the_question_is_the_approved_copy_in_both_languages():
    from applire.services.profile.reconcile.stance import (
        offered_choice_denial_confirmation,
        offered_choice_denial_hits,
    )

    (hit,) = offered_choice_denial_hits(_vault(_founder_shape_rows()))
    conf = offered_choice_denial_confirmation(hit)
    assert conf.question_i18n["de"] == (
        "Am 02.11.2026 hast du im Interview eine vorgeschlagene Antwort gewählt. Diese Antwort sagte "
        "nur, dass in deinem Profil zu Boomi, Kafka, AWS, iPaaS, ESB und Referenzarchitekturen noch "
        "nichts steht, nicht dass dir das fehlt. Gespeichert wurde es trotzdem als „trifft nicht zu“. "
        "Sollen wir das zurücknehmen?"
    )
    assert conf.question_i18n["en"] == conf.question == (
        "On 2 Nov 2026 you picked a suggested answer in the interview. It only said that your profile "
        "does not mention Boomi, Kafka, AWS, iPaaS, ESB and Referenzarchitekturen yet, not that you "
        "lack them. It was still saved as “does not apply”. Should we take that back?"
    )
    assert conf.options_i18n == [
        {"de": "Ja, zurücknehmen", "en": "Yes, take it back"},
        {"de": "Doch, das fehlt mir", "en": "No, I do lack that"},
    ]
    assert conf.option_keys == ["not_a_no", "lacks_it"]
    assert conf.context["kind"] == "denial_scope" and conf.context["conflict_kind"] == "offered_choice"


def test_without_a_date_the_question_still_reads():
    from applire.services.profile.reconcile.stance import offered_choice_denial_confirmation

    conf = offered_choice_denial_confirmation({"denial_concepts": ["Boomi"], "statement_key": "k"})
    assert conf.question_i18n["de"].startswith("Im Interview hast du eine vorgeschlagene Antwort gewählt.")
    assert "zu Boomi noch nichts" in conf.question_i18n["de"]
    assert conf.question_i18n["en"].startswith("In the interview you picked a suggested answer. ")


def test_the_parker_parks_once_and_never_after_an_answer():
    from applire.schemas.profile import MasterProfileData
    from applire.services.profile.reconcile.stance import (
        denial_scope_key,
        park_offered_choice_confirmations,
    )

    profile = MasterProfileData.model_validate(_vault(_founder_shape_rows()))
    pj = profile.model_dump(mode="json")
    assert len(park_offered_choice_confirmations(profile.metadata, pj)) == 1
    assert park_offered_choice_confirmations(profile.metadata, pj) == [], "open: not again"
    key = denial_scope_key(profile.metadata.pending_confirmations[0].context)
    assert key.startswith("offered_choice|")
    profile.metadata.pending_confirmations = []
    profile.metadata.denial_scope_answered = [key]
    assert park_offered_choice_confirmations(profile.metadata, pj) == [], "answered: not again"


def test_the_cv_conflict_key_is_unchanged():
    """The G-5 item's identity stays what it was (its answered keys are stored)."""
    from applire.services.profile.reconcile.stance import denial_scope_key

    assert denial_scope_key({"denial_concept": "Ansible", "bare_term": "ansible"}) == "ansible|ansible"


# ---------------------------------------------------------------------------
# 3. Seams: committer, Health hub, the two answers
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_session():
    from applire.db.session import Base
    from applire.models.profile import MasterProfile, ProfileSnapshot

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=[MasterProfile.__table__, ProfileSnapshot.__table__])
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(db, seed):
    from applire.models.profile import MasterProfile, authorized_profile_write

    with authorized_profile_write():
        record = MasterProfile(profile_json=copy.deepcopy(seed))
    db.add(record)
    await db.commit()
    return record


async def _ordinary_write(db, record):
    from applire.services.profile.commit import CommitProvenance, commit_ops
    from applire.services.profile.reconcile.ops import UpsertSkill

    await commit_ops(
        db, [UpsertSkill(name="Terraform", category="technical")],
        CommitProvenance(source="manual_edit", intake="skill_edit", session_id=str(uuid.uuid4())),
        record=record,
    )
    await db.commit()
    await db.refresh(record)


def _offered_items(profile_json):
    return [c for c in profile_json["metadata"]["pending_confirmations"]
            if (c.get("context") or {}).get("conflict_kind") == "offered_choice"]


@pytest.mark.asyncio
async def test_the_committer_parks_the_question_on_an_ordinary_write(db_session):
    record = await _seed(db_session, _vault(_founder_shape_rows()))
    await _ordinary_write(db_session, record)
    (item,) = _offered_items(record.profile_json)
    assert item["context"]["denial_concepts"] == CLAUSE_TERMS
    await _ordinary_write(db_session, record)
    assert len(_offered_items(record.profile_json)) == 1, "the next write does not park it again"


@pytest.mark.asyncio
async def test_a_later_typed_redenial_through_the_committer_is_not_offered(db_session):
    """Adversarial H-1, on the real write path: the chip row's statement is
    write-once, so only the later turn's receipt tells the two apart."""
    from applire.services.profile.commit import CommitProvenance, TurnGrounding, commit_ops

    record = await _seed(db_session, _vault([_row(t, CHIP) for t in CLAUSE_TERMS]))
    typed = "Mit Kafka habe ich wirklich nie gearbeitet."
    await commit_ops(
        db_session, [],
        CommitProvenance(source="interview", intake="interview_turn", session_id=str(uuid.uuid4())),
        record=record,
        grounding=TurnGrounding(text=typed, question="Und Kafka?", gap="Kafka", denials=["Kafka"]),
    )
    await db_session.commit()
    await db_session.refresh(record)
    kafka = next(d for d in record.profile_json["metadata"]["denied_concepts"] if d["concept"] == "Kafka")
    assert kafka["statement"] == CHIP, "write-once (#348): the chip's words stay"
    (item,) = _offered_items(record.profile_json)
    assert "Kafka" not in item["context"]["denial_concepts"]
    assert len(item["context"]["denial_concepts"]) == 5


@pytest.mark.asyncio
async def test_an_open_question_drops_a_concept_the_candidate_later_denies(db_session):
    """The item was parked first (at upgrade or an earlier write); a later
    typed re-denial of Kafka replaces it with one that no longer offers Kafka."""
    from applire.services.profile.commit import CommitProvenance, TurnGrounding, commit_ops

    record = await _seed(db_session, _vault([_row(t, CHIP) for t in CLAUSE_TERMS]))
    await _ordinary_write(db_session, record)
    (before,) = _offered_items(record.profile_json)
    assert "Kafka" in before["context"]["denial_concepts"]
    await commit_ops(
        db_session, [],
        CommitProvenance(source="interview", intake="interview_turn", session_id=str(uuid.uuid4())),
        record=record,
        grounding=TurnGrounding(text="Kafka nie.", question="Und Kafka?", gap="Kafka", denials=["Kafka"]),
    )
    await db_session.commit()
    await db_session.refresh(record)
    (after,) = _offered_items(record.profile_json)
    assert after["confirmation_id"] != before["confirmation_id"]
    assert after["context"]["denial_concepts"] == [t for t in CLAUSE_TERMS if t != "Kafka"]
    await _ordinary_write(db_session, record)
    (again,) = _offered_items(record.profile_json)
    assert again["confirmation_id"] == after["confirmation_id"], "an unchanged item stays as it is"


def test_the_health_hub_answers_it_in_place():
    from applire.schemas.profile import MasterProfileData
    from applire.services.profile.health import assess_health
    from applire.services.profile.reconcile.stance import (
        offered_choice_denial_confirmation,
        offered_choice_denial_hits,
    )

    seed = _vault(_founder_shape_rows())
    (hit,) = offered_choice_denial_hits(seed)
    conf = offered_choice_denial_confirmation(hit)
    seed["metadata"]["pending_confirmations"] = [conf.model_dump(mode="json")]
    (issue,) = [i for i in assess_health(MasterProfileData.model_validate(seed)).issues
                if i.confirmation_id == conf.confirmation_id]
    assert issue.confirmation_kind == "denial_scope"
    assert issue.options == ["Yes, take it back", "No, I do lack that"]
    assert issue.question_i18n["de"].startswith("Am 02.11.2026")


async def _answer(db, option, *, skills=None):
    from applire.services.profile import resolve_confirmation

    record = await _seed(db, _vault(_founder_shape_rows(), skills=skills))
    await _ordinary_write(db, record)
    (item,) = _offered_items(record.profile_json)
    response = await resolve_confirmation(item["confirmation_id"], option, db)
    return response.profile


@pytest.mark.asyncio
@pytest.mark.parametrize("option", ["Ja, zurücknehmen", "Yes, take it back"])
async def test_take_it_back_withdraws_every_concept_of_the_statement_only(db_session, option):
    skills = [
        {"id": "s-kafka", "name": "Kafka", "category": "technical", "status": "denied"},
        {"id": "s-py", "name": "Python", "category": "technical", "status": "confirmed"},
    ]
    profile = await _answer(db_session, option, skills=skills)
    meta = profile.metadata
    assert sorted(d.concept for d in meta.withdrawn_denials) == sorted(CLAUSE_TERMS)
    assert [(d.concept, d.statement) for d in meta.denied_concepts] == [("Boomi", TYPED)], (
        "the typed Boomi denial (its own statement) stays"
    )
    kafka = next(s for s in profile.skills if s.name == "Kafka")
    assert kafka.status == "unconfirmed", "revoking is not claiming (G-5)"
    assert not [c for c in meta.pending_confirmations if (c.context or {}).get("conflict_kind")]


@pytest.mark.asyncio
@pytest.mark.parametrize("option", ["Doch, das fehlt mir", "No, I do lack that"])
async def test_i_do_lack_it_keeps_them_and_is_never_asked_again(db_session, option):
    from applire.models.profile import MasterProfile

    profile = await _answer(db_session, option)
    meta = profile.metadata
    assert len(meta.denied_concepts) == len(CLAUSE_TERMS) + 1
    assert meta.withdrawn_denials == []
    assert any(k.startswith("offered_choice|") for k in meta.denial_scope_answered)
    record = (await db_session.execute(sa.select(MasterProfile))).scalars().one()
    await _ordinary_write(db_session, record)
    assert _offered_items(record.profile_json) == []


# ---------------------------------------------------------------------------
# 4. The 0086 data migration
# ---------------------------------------------------------------------------


def _migration():
    spec = importlib.util.spec_from_file_location("m0086", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_PARITY_STATEMENTS = [
    CHIP, CHIP_EN, MIXED, TYPED,
    "Ich habe Power-BI-Dashboards gebaut; zu Self-Service-BI steht in meinem Profil bisher nichts.",
    "Ich kenne Verpackungslinien, aber weitere Verpackungsbranchen sind in meinem Profil noch nicht dokumentiert.",
    "I ran the billing platform, but my profile doesn't cover PSD2 yet. I have not used BaFin reporting.",
    "Mein Profil ist vollständig.",
]
_PARITY_CONCEPTS = CLAUSE_TERMS + [
    "reference architectures", "Self-Service-BI", "Verpackungsbranchen", "PSD2", "BaFin reporting", "SAP S/4HANA",
]


_INFLECTED_STATEMENTS = [
    "Ich habe Unterweisungen gemacht; zu ISO 45001 und Arbeitssicherheitsmanagementsystemen steht in "
    "meinem Profil noch nichts.",
    "Ich habe REST-APIs gebaut, aber zu Kafka ist in meinem Profil noch nichts dokumentiert.",
    "I built REST APIs, but Kafka and SLOs are not yet documented in my profile.",
    CHIP.rstrip(".") + ", und mit Kafka habe ich tatsächlich nie gearbeitet.",
]
_INFLECTED_CONCEPTS = ["Arbeitssicherheitsmanagementsysteme", "ISO 45001", "SLO", "Referenzarchitektur", "LAWS"]


def test_the_migrations_copy_agrees_with_the_app():
    """A migration never imports application code, so it carries a copy; the
    copy and the app must not drift apart before the migration ships."""
    from applire.services.profile.reconcile import stance

    mod = _migration()
    rows = [_row(c, s, date=f"2026-11-0{i % 9 + 1}")
            for i, s in enumerate(_PARITY_STATEMENTS) for c in _PARITY_CONCEPTS]
    rows += [_row(c, CHIP, source="agent_interview") for c in CLAUSE_TERMS]
    rows += [_row(c, s) for s in _INFLECTED_STATEMENTS for c in _INFLECTED_CONCEPTS]
    vault = _vault(rows)
    vault["metadata"]["enrichment_history"] = [{"changes": [
        {"section": "metadata", "field": "denied_concepts", "action": "updated", "new_value": "ESB"},
        {"section": "skills", "field": "denied_concepts", "action": "updated", "new_value": "iPaaS"},
    ]}]
    for s in _PARITY_STATEMENTS + _INFLECTED_STATEMENTS:
        assert mod.profile_silence_clauses(s) == stance.profile_silence_clauses(s)
        for c in _PARITY_CONCEPTS + _INFLECTED_CONCEPTS:
            assert mod.inflected_present(c, s.lower()) == stance.inflected_present(c, s.lower())
    app_hits, mig_hits = stance.offered_choice_denial_hits(vault), mod.offered_choice_denial_hits(vault)
    assert app_hits == mig_hits and len(app_hits) >= 5
    for hit in app_hits:
        app = stance.offered_choice_denial_confirmation(hit).model_dump(mode="json")
        mig = mod.offered_choice_denial_confirmation(hit)
        app.pop("confirmation_id"), mig.pop("confirmation_id")
        assert mig == app
        assert mod._key_of(mig["context"]) == stance.denial_scope_key(app["context"])


def test_the_migration_chains_onto_0085():
    mod = _migration()
    assert (mod.revision, mod.down_revision) == ("0086", "0085")


def test_the_migration_parks_and_unparks_on_a_real_database():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mod = _migration()
    engine = sa.create_engine("sqlite://")
    meta = sa.MetaData()
    table = sa.Table(
        "master_profiles", meta,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("profile_json", sa.JSON, nullable=False),
        sa.Column("deleted_at", sa.DateTime, nullable=True),
    )
    meta.create_all(engine)
    answered_key = None
    with engine.begin() as conn:
        conn.execute(table.insert(), [
            {"id": "live", "profile_json": _vault(_founder_shape_rows()), "deleted_at": None},
            {"id": "typed-only", "profile_json": _vault([_row("Boomi", TYPED)]), "deleted_at": None},
            {"id": "deleted", "profile_json": _vault(_founder_shape_rows()),
             "deleted_at": datetime(2026, 11, 1)},
        ])
    from applire.services.profile.reconcile.stance import offered_choice_denial_hits

    answered_key = "offered_choice|" + offered_choice_denial_hits(_vault(_founder_shape_rows()))[0]["statement_key"]
    with engine.begin() as conn:
        conn.execute(table.insert(), [{
            "id": "answered", "profile_json": _vault(_founder_shape_rows(), answered=[answered_key]),
            "deleted_at": None,
        }])

    def run(step):
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                step()
        with engine.connect() as conn:
            return {
                r.id: (json.loads(r.profile_json) if isinstance(r.profile_json, str) else r.profile_json)
                for r in conn.execute(sa.select(table))
            }

    after = run(mod.upgrade)
    (item,) = _offered_items(after["live"])
    assert item["context"]["denial_concepts"] == CLAUSE_TERMS
    assert after["live"]["metadata"]["denied_concepts"] == _vault(_founder_shape_rows())["metadata"]["denied_concepts"], (
        "nothing is withdrawn at upgrade — only the question is parked"
    )
    assert _offered_items(after["typed-only"]) == [], "a typed denial is never offered"
    assert _offered_items(after["deleted"]) == []
    assert _offered_items(after["answered"]) == []

    from applire.schemas.profile import MasterProfileData

    MasterProfileData.model_validate(after["live"])  # the app loads what the migration wrote

    again = run(mod.upgrade)
    assert len(_offered_items(again["live"])) == 1, "idempotent"

    down = run(mod.downgrade)
    assert _offered_items(down["live"]) == []
    assert down["live"]["metadata"]["denied_concepts"] == after["live"]["metadata"]["denied_concepts"]
