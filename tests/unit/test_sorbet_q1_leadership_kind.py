# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet WP-Q1 cluster (b), ruling Q-2 as narrowed by MD-Q5: the CV's per-role
fact line names the KIND of leadership the person ANSWERED, or only the count.

Platform-1 delivery run (#832): Katrin's vault said "Fachliche Anleitung von zwei
Werkstudierenden"; the delivered CV printed "Führungsverantwortung für 3 Personen".
Q-2 first read the kind from her bullets; two adversarial rounds (MD-Q3 H1, the
re-check N1–N3) showed every prose reading either promoted a mention to a fact or
demoted a true form. MD-Q5: the kind is a STORED fact on the work entry, written
only from the person's answer to the dedicated question; the bullets are only
shown in that question.

Seams: the writer (services/profile/leadership_kind.py through the real
committer), the reader (role_facts.leadership_kind), the single tailored-CV
writer (cv._apply_role_facts), the shared phrase producer
(templates.filters.role_facts — every template and the .docx), the section door,
the prompt view, and the planner helper Q2 asks from.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.schemas.cv import TailoredCVData
from applire.schemas.profile import MasterProfileData
from applire.services.cv import _apply_role_facts
from applire.services.profile.leadership_kind import (
    KIND_OF_OPTION,
    OPTION_LABELS,
    LeadershipKindEntryNotFound,
    apply_leadership_kind_op,
    leadership_kind_op,
    option_for_answer,
)
from applire.services.profile.role_facts import (
    entries_needing_leadership_kind,
    leadership_kind,
    leadership_said,
)
from applire.templates.filters import role_facts

NEUTRAL_DE = "12 Personen im Verantwortungsbereich"
DISCIPLINARY_DE = "Disziplinarische Führung von 12 Mitarbeitenden"
FUNCTIONAL_DE = "Fachliche Anleitung von 12 Personen"


def _e(*bullets: str, **extra) -> dict:
    return {"id": extra.pop("id", "w1"), "company": extra.pop("company", "X GmbH"),
            "role": extra.pop("role", "Controller"), "responsibilities": list(bullets), **extra}


def _line(entry: dict, lang: str = "de") -> str:
    """The delivered line: vault entry → the single tailored writer → the phrase."""
    tailored = TailoredCVData.model_validate({
        "contact": {"name": "Test"},
        # The draft claims "disciplinary" — it never survives the writer.
        "work_history": [{"id": entry["id"], "company": "X", "role": "R", "bullets": ["b"],
                          "leadership_kind": "disciplinary"}],
    })
    out = _apply_role_facts(tailored, {"work_experience": [entry]})
    return role_facts(out.work_history[0], lang)


# ── the re-check's inputs (adv/findings.md N1–N3): prose never sets the kind ──

N1_FALSE_DISCIPLINARY = [
    "Wir hatten 12 direct reports unter dem alten Management.",
    "Disziplinarische Führung von 12 Mitarbeitenden wurde von meinem Nachfolger übernommen.",
    "Disziplinarische Führung von 12 Mitarbeitenden hatte ich nicht.",
    "Disziplinarische Führung von 12 Mitarbeitenden war nicht Teil meiner Rolle.",
    "Aspiring to line management of 8 engineers.",
    "Line management of six engineers was transferred to Anna after reorganisation.",
    "Supported my colleague with the line management of 5 employees.",
    "Mentored the line manager of 6 engineers.",
    "Coaching für Führungskräfte mit Personalverantwortung für 20 Mitarbeitende.",
    "Konzept zur Personalverantwortung für Nachwuchskräfte erstellt.",
    "Team führte disziplinarisch 14 Mitarbeitende, ich fachlich.",
]
N2_BOTH = ["Fachliche und disziplinarische Führung von 12 Mitarbeitenden."]
N3_TRUE_FORMS = [
    "Leitete 3 Werkstudierende fachlich an.",
    "Teamleiter mit disziplinarischer Verantwortung für 12 Mitarbeitende.",
    "Führung eines 12-köpfigen Teams (disziplinarisch).",
    "Disziplinarischer Vorgesetzter von 12 Mitarbeitenden.",
    "Fachliche Teamleitung für 4 Personen.",
    "Fachliche Verantwortung für ein Team von 3.",
    "Technical lead for a team of 3 engineers.",
    "Line management responsibility for 12 engineers.",
    "Verantwortung für 12 Mitarbeitende inkl. Personalverantwortung.",
    "Personalverantwortung (12 MA).",
]
EARLIER_TRUE_FORMS = [
    "Fachliche Anleitung von zwei Werkstudierenden.",
    "Disziplinarische Führung von 12 Mitarbeitenden.",
    "Personalverantwortung für acht Mitarbeitende.",
    "Line manager of six engineers.",
]


@pytest.mark.parametrize("bullet", N1_FALSE_DISCIPLINARY + N2_BOTH + N3_TRUE_FORMS + EARLIER_TRUE_FORMS)
def test_with_no_stored_answer_every_bullet_prints_the_neutral_line(bullet):
    entry = _e(bullet, team_size=12)
    assert leadership_kind(entry) is None
    assert _line(entry) == NEUTRAL_DE
    assert _line(entry, "en") == "12 people in area of responsibility"


@pytest.mark.parametrize("bullet", N1_FALSE_DISCIPLINARY + N2_BOTH + N3_TRUE_FORMS)
def test_every_such_role_is_asked_once_and_sees_its_own_words(bullet):
    (asked,) = entries_needing_leadership_kind({"work_experience": [_e(bullet, team_size=12)]})
    assert asked["said"] == bullet


def test_the_title_is_never_read_for_the_kind():
    assert leadership_kind(_e("Monatsabschluss.", role="Disziplinarischer Teamleiter", team_size=4)) is None


# ── the options (RULINGS MD-Q5) ──────────────────────────────────────────────

@pytest.mark.parametrize("answer, option, line_de, line_en", [
    ("Disziplinarisch (Personalverantwortung)", "disciplinary", DISCIPLINARY_DE, "Line management of 12 employees"),
    ("Beides", "both", DISCIPLINARY_DE, "Line management of 12 employees"),
    ("Fachliche Anleitung", "functional", FUNCTIONAL_DE, "Functional guidance of 12 people"),
    ("Disciplinary (line management)", "disciplinary", DISCIPLINARY_DE, "Line management of 12 employees"),
    ("Both", "both", DISCIPLINARY_DE, "Line management of 12 employees"),
    ("Functional guidance", "functional", FUNCTIONAL_DE, "Functional guidance of 12 people"),
    ("  beides ", "both", DISCIPLINARY_DE, "Line management of 12 employees"),
    ("eher fachlich, manchmal auch disziplinarisch", "own_words", NEUTRAL_DE, "12 people in area of responsibility"),
    ("Disziplinarisch", "own_words", NEUTRAL_DE, "12 people in area of responsibility"),
    ("", "skipped", NEUTRAL_DE, "12 people in area of responsibility"),
    (None, "skipped", NEUTRAL_DE, "12 people in area of responsibility"),
])
def test_each_answer_prints_its_line(answer, option, line_de, line_en):
    assert option_for_answer(answer) == option
    profile = MasterProfileData.model_validate({"work_experience": [_e("Direkte Führung.", team_size=12)]})
    changes: list = []
    apply_leadership_kind_op(leadership_kind_op("w1", option, answer=answer or ""), profile, changes)
    entry = profile.model_dump(mode="json")["work_experience"][0]
    assert leadership_kind(entry) == KIND_OF_OPTION[option]
    assert entry["leadership_kind_record"]["option"] == option
    assert _line(entry) == line_de
    assert _line(entry, "en") == line_en
    # Asked either way: never asked again.
    assert entries_needing_leadership_kind({"work_experience": [entry]}) == []
    (change,) = changes
    assert (change.section, change.field, change.entity_id, change.new_value) == (
        "work_experience", "leadership_kind", "w1", KIND_OF_OPTION[option],
    )
    assert change.rationale_key == (
        "leadership_kind_recorded" if KIND_OF_OPTION[option] else "leadership_kind_left_open"
    )


def test_the_option_labels_are_the_rulings():
    assert OPTION_LABELS["de"] == {
        "disciplinary": "Disziplinarisch (Personalverantwortung)",
        "functional": "Fachliche Anleitung",
        "both": "Beides",
    }
    assert KIND_OF_OPTION == {"disciplinary": "disciplinary", "both": "disciplinary",
                              "functional": "functional", "own_words": None, "skipped": None}


def test_an_entry_stored_without_an_id_is_addressed_by_company_and_role():
    profile = MasterProfileData.model_validate({"work_experience": [
        {"company": "Weberit", "role": "Meister", "team_size": 14},
        {"company": "Rasselstein", "role": "Meister", "team_size": 9},
    ]})
    apply_leadership_kind_op(
        leadership_kind_op("", "functional", company="weberit", role="Meister"), profile, [],
    )
    assert [e.leadership_kind for e in profile.work_experience] == ["functional", None]


def test_an_unknown_or_ambiguous_role_is_refused():
    profile = MasterProfileData.model_validate({"work_experience": [
        _e(id="a", company="X", role="R"), _e(id="b", company="X", role="R"),
    ]})
    for op in (leadership_kind_op("nope", "both"), leadership_kind_op("", "both", company="X", role="R")):
        with pytest.raises(LeadershipKindEntryNotFound):
            apply_leadership_kind_op(op, profile, [])


def test_the_op_is_never_model_emittable():
    from pydantic import TypeAdapter, ValidationError

    from applire.services.profile.reconcile.ops import CommitOp, DecisionOp, ReconcileOp

    payload = {"op": "record_leadership_kind", "entry_id": "w1", "option": "both"}
    with pytest.raises(ValidationError):
        TypeAdapter(ReconcileOp).validate_python(payload)
    assert TypeAdapter(DecisionOp).validate_python(payload).option == "both"
    assert TypeAdapter(CommitOp).validate_python(payload).option == "both"


# ── through the real committer ───────────────────────────────────────────────

@pytest_asyncio.fixture
async def db_session():
    from applire.db.session import Base
    from applire.models.profile import MasterProfile, ProfileSnapshot

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=[MasterProfile.__table__, ProfileSnapshot.__table__])
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _vault(db_session, work):
    from applire.models.profile import MasterProfile, authorized_profile_write

    with authorized_profile_write():
        record = MasterProfile(profile_json={
            "personal_info": {"name": "Katrin Vogt"}, "work_experience": work, "metadata": {},
        })
    db_session.add(record)
    await db_session.commit()
    return record


@pytest.mark.asyncio
async def test_the_writer_commits_the_answer_with_its_receipt(db_session):
    from applire.services.profile.leadership_kind import record_leadership_kind

    record = await _vault(db_session, [_e("Fachliche Anleitung von zwei Werkstudierenden.", team_size=3)])
    result = await record_leadership_kind(
        db_session, entry_id="w1", option=option_for_answer("Fachliche Anleitung"),
        answer="Fachliche Anleitung",
    )
    await db_session.refresh(record)
    (entry,) = record.profile_json["work_experience"]
    assert entry["leadership_kind"] == "functional"
    assert entry["leadership_kind_record"]["answer"] == "Fachliche Anleitung"
    assert entry["leadership_kind_record"]["source"] == "interview"
    assert entry["leadership_kind_record"]["recorded_at"]
    last = record.profile_json["metadata"]["enrichment_history"][-1]
    assert last["source"] == "interview"
    assert [c["field"] for c in last["changes"]] == ["leadership_kind"]
    assert result.record is record
    assert _line(entry) == "Fachliche Anleitung von 3 Personen"


@pytest.mark.asyncio
async def test_the_writer_refuses_an_unknown_role_and_a_foreign_source(db_session):
    from applire.services.profile.leadership_kind import record_leadership_kind

    await _vault(db_session, [_e(team_size=3)])
    with pytest.raises(LeadershipKindEntryNotFound):
        await record_leadership_kind(db_session, entry_id="nope", option="both")
    with pytest.raises(ValueError):
        await record_leadership_kind(db_session, entry_id="w1", option="both", source="cv_upload")


@pytest.mark.asyncio
async def test_the_section_door_can_neither_set_nor_wipe_the_kind(db_session):
    from applire.services.profile.commit import CommitProvenance, commit_ops
    from applire.services.profile.leadership_kind import record_leadership_kind
    from applire.services.profile.reconcile.ops import ReplaceSection

    record = await _vault(db_session, [_e(team_size=12), _e(id="w2", company="Y", team_size=5)])
    await record_leadership_kind(db_session, entry_id="w1", option="both", answer="Beides")
    await db_session.refresh(record)
    # An editor payload that drops w1's kind and claims one for w2.
    payload = [
        {k: v for k, v in e.items() if not k.startswith("leadership_kind")}
        for e in record.profile_json["work_experience"]
    ]
    payload[1]["leadership_kind"] = "disciplinary"
    await commit_ops(
        db_session, [ReplaceSection(section="work_experience", value=payload)],
        CommitProvenance(source="manual_edit", intake="section_edit"), record=record,
    )
    await db_session.commit()
    await db_session.refresh(record)
    w1, w2 = record.profile_json["work_experience"]
    assert w1["leadership_kind"] == "disciplinary" and w1["leadership_kind_record"]["option"] == "both"
    assert "leadership_kind" not in w2 and "leadership_kind_record" not in w2


# ── the readers ──────────────────────────────────────────────────────────────

def test_an_unasked_profile_dumps_without_the_fields():
    dumped = MasterProfileData.model_validate({"work_experience": [_e(team_size=3)]}).model_dump(mode="json")
    assert "leadership_kind" not in dumped["work_experience"][0]
    assert "leadership_kind_record" not in dumped["work_experience"][0]


def test_the_prompt_view_carries_the_kind_and_not_its_bookkeeping():
    from applire.services.prompt_view import prompt_profile_view

    profile = MasterProfileData.model_validate({"work_experience": [_e(team_size=12)]})
    apply_leadership_kind_op(leadership_kind_op("w1", "both", answer="Beides"), profile, [])
    raw = profile.model_dump(mode="json")
    view = prompt_profile_view(raw)
    assert view["work_experience"][0]["leadership_kind"] == "disciplinary"
    assert "leadership_kind_record" not in view["work_experience"][0]
    assert "leadership_kind_record" in raw["work_experience"][0]  # caller's dict untouched


def test_the_single_writer_carries_the_stored_kind_and_never_the_drafts():
    profile = {"work_experience": [
        _e("Direkte Führung.", id="w1", team_size=3, leadership_kind="functional"),
        _e("Disziplinarische Führung von 38 Mitarbeitenden.", id="w2", team_size=38),
        _e("Disziplinarische Führung des Teams.", id="w3", leadership_kind="disciplinary"),
    ]}
    tailored = TailoredCVData.model_validate({
        "contact": {"name": "Test"},
        "work_history": [
            {"id": i, "company": "X", "role": "R", "bullets": ["b"], "leadership_kind": "disciplinary"}
            for i in ("w1", "w2", "w3", "w-unmatched")
        ],
    })
    out = _apply_role_facts(tailored, profile)
    assert {w.id: (w.team_size, w.leadership_kind) for w in out.work_history} == {
        "w1": (3, "functional"),
        "w2": (38, None),
        "w3": (None, None),          # no headcount → no kind on the line
        "w-unmatched": (None, None),
    }


@pytest.mark.parametrize("kind, lang, n, line", [
    ("functional", "de", 3, "Fachliche Anleitung von 3 Personen"),
    ("functional", "de", 1, "Fachliche Anleitung von 1 Person"),
    ("disciplinary", "de", 38, "Disziplinarische Führung von 38 Mitarbeitenden"),
    (None, "de", 38, "38 Personen im Verantwortungsbereich"),
    (None, "de", 1, "1 Person im Verantwortungsbereich"),
    ("functional", "en", 3, "Functional guidance of 3 people"),
    ("disciplinary", "en", 38, "Line management of 38 employees"),
    (None, "en", 14, "14 people in area of responsibility"),
    ("unknown-kind", "de", 5, "5 Personen im Verantwortungsbereich"),
])
def test_the_line_names_the_kind_or_only_the_count(kind, lang, n, line):
    assert role_facts({"team_size": n, "leadership_kind": kind}, lang) == line


def test_the_retired_phrase_is_never_produced():
    for lang in ("de", "en"):
        for kind in ("functional", "disciplinary", None):
            out = role_facts({"team_size": 12, "leadership_kind": kind}, lang)
            assert "Führungsverantwortung" not in out and "Responsible for a team" not in out


# ── the planner helper (MD-Q1 contract + MD-Q5 "said") ───────────────────────

def test_the_planner_helper_lists_sized_unasked_roles_with_no_stored_kind():
    profile = {"work_experience": [
        _e("Fachliche Anleitung von zwei Werkstudierenden.", id="k", team_size=3),
        _e("Direkte Führung von 38 Mitarbeitenden.", id="s", team_size=38),
        _e("Schichtführung.", id="z", team_size=0),
        _e("Kein Team.", id="n"),
        _e(id="f", team_size=4, leadership_kind="functional"),
        _e(id="o", team_size=4, leadership_kind_record={"option": "own_words", "answer": "eher beides"}),
    ]}
    assert entries_needing_leadership_kind(profile) == [
        {"entry_id": "k", "company": "X GmbH", "role": "Controller", "team_size": 3,
         "said": "Fachliche Anleitung von zwei Werkstudierenden."},
        {"entry_id": "s", "company": "X GmbH", "role": "Controller", "team_size": 38,
         "said": "Direkte Führung von 38 Mitarbeitenden."},
    ]


def test_the_planner_helper_contract_shape_is_pinned():
    """MD-Q1 + MD-Q5: WP-Q2's planner builds its question from exactly these keys —
    entry_id, company, role, team_size (an int), said (a str) — in entry order."""
    raw = {"work_experience": [
        _e("Direkte Führung von 38 Mitarbeitenden.", id="a", team_size=38, company="Weberit"),
        _e("Monatsabschluss.", id="b", team_size=14, company="Rasselstein", role="Meister"),
    ]}
    for profile in (raw, MasterProfileData.model_validate(raw)):
        got = entries_needing_leadership_kind(profile)
        assert [list(g) for g in got] == [["entry_id", "company", "role", "team_size", "said"]] * 2
        assert [g["entry_id"] for g in got] == ["a", "b"]
        assert got[0] == {"entry_id": "a", "company": "Weberit", "role": "Controller", "team_size": 38,
                          "said": "Direkte Führung von 38 Mitarbeitenden."}
        assert got[1]["said"] == ""
        assert all(type(g["team_size"]) is int for g in got)


def test_said_reads_achievements_too():
    entry = _e("Monatsabschluss.", achievements=["Personalverantwortung für 4 MA übernommen."])
    assert leadership_said(entry) == "Personalverantwortung für 4 MA übernommen."
    assert leadership_said(_e("Monatsabschluss.")) == ""


# ── delivery re-run (Stefan): "said" quotes only a bullet about leading PEOPLE ──

STEFAN_AUDIT = "Begleitung der jährlichen ISO-9001-Audits als Bereichsverantwortlicher."


@pytest.mark.parametrize("bullet", [
    STEFAN_AUDIT,
    "Einleitung von Korrekturmaßnahmen nach Audits.",
    "Weiterleitung von Tickets an den 2nd Level.",
    # a people word near a "-leitung" that leads nobody
    "Weiterleitung von Tickets an das Team.",
    "Begleitung des Teams bei der ISO-Zertifizierung.",
    "Einleitung der Schulung für 12 Mitarbeitende.",
    "Umleitung der Materialflüsse während des Umbaus.",
    "Anleitung zur Bedienung der Anlage erstellt.",
    "Leitung der Monatsberichte.",
    "Projektleitung SAP-Einführung.",
    "Fachliche Verantwortung für das Reporting.",
    "Managed the migration to FastAPI.",
])
def test_said_never_quotes_a_bullet_that_leads_no_people(bullet):
    assert leadership_said(_e(bullet, team_size=38)) == ""


@pytest.mark.parametrize("bullet", [
    "Fachliche Anleitung von 3 Werkstudierenden",
    "Direkte Führung von 38 Mitarbeitenden über drei Schichtleiter",
    "Leitete 3 Werkstudierende fachlich an.",
    "Technical lead for a team of 3 engineers.",
    "Personalverantwortung (12 MA).",
    "Verantwortung für zwei Fertigungsbereiche mit 38 Mitarbeitenden.",
])
def test_said_quotes_a_bullet_that_leads_people(bullet):
    assert leadership_said(_e(STEFAN_AUDIT, bullet, team_size=38)) == bullet


def test_said_prefers_the_bullet_that_counts_the_team_size():
    entry = _e(
        STEFAN_AUDIT,
        "Betreuung von zwei Werkstudierenden.",
        "Verantwortung für zwei Fertigungsbereiche mit 38 Mitarbeitenden.",
        team_size=38,
    )
    assert leadership_said(entry) == "Verantwortung für zwei Fertigungsbereiche mit 38 Mitarbeitenden."
    # No bullet counts the team size: the first with a people count.
    assert leadership_said(_e("Coaching des Teams.", "Betreuung von zwei Werkstudierenden.", team_size=5)) == (
        "Betreuung von zwei Werkstudierenden."
    )
    # No count at all: the first that qualifies.
    assert leadership_said(_e("Projektleitung SAP.", "Coaching des Teams.", team_size=5)) == "Coaching des Teams."


def test_stefans_role_is_asked_without_the_audit_quote():
    (asked,) = entries_needing_leadership_kind({"work_experience": [_e(STEFAN_AUDIT, team_size=38)]})
    assert asked["said"] == ""


@pytest.mark.parametrize("raw", ["both", "Disziplinarisch", "own_words", "", 1, True])
def test_the_reader_returns_only_a_stored_kind(raw):
    assert leadership_kind({"team_size": 4, "leadership_kind": raw}) is None
