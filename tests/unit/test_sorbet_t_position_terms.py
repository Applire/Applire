# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#831 (Sorbet WP-T; ADR-046 amended 2026-11-13; founder RULING T-1): a
reconcile paraphrase never raises the person's position silently.

The recorded turn (UX audit 4, 2026-11-07, Sonnet 5.5, testimony door), names
swapped for invented ones: the person wrote "Verantwortlich zeichnet unsere
Leiterin Rechnungswesen — ich bin die operative Nummer zwei dafür"; the
reconciler wrote "…; operative Stellvertreterin der Leiterin Rechnungswesen, die
verantwortlich zeichnet". T-1: the narrow check — a title tied to an
organisational unit — HOLDS the bullet and asks once; a role within a task
passes; the answer writes the title or the task-scoped wording.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.schemas.profile import MasterProfileData, PendingConfirmation, ProfileMetadata
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.engine import _parse_ops, reconcile
from applire.services.profile.reconcile.ops import (
    AddBullets,
    RequestConfirmation,
    ResolveConfirmation,
    SetField,
    UpsertWork,
)
from applire.services.profile.reconcile.position_terms import (
    enforce_position_terms,
    position_claims,
)

ANSWER = (
    "## Konsolidierung / LucaNet\n"
    "Bei der Einführung von LucaNet in unserer kleinen Gruppe (GmbH + Vertriebs-\n"
    "gesellschaft Schweiz) habe ich das Teilprojekt Intercompany-Abstimmung und\n"
    "Kapitalkonsolidierung verantwortet und erstelle seit 2023 die konsolidierten\n"
    "Quartalszahlen darin mit. Verantwortlich zeichnet unsere Leiterin Rechnungswesen —\n"
    "ich bin die operative Nummer zwei dafür.\n\n"
    "## Führung\n"
    "Fachlich führe ich seit zwei Jahren die beiden Werkstudierenden. Disziplinarische\n"
    "Führung hatte ich noch nicht.\n"
)
DEPUTY_BULLET = (
    "Erstellt seit 2023 die konsolidierten Quartalszahlen in LucaNet mit; operative "
    "Stellvertreterin der Leiterin Rechnungswesen, die verantwortlich zeichnet"
)
TASK_TEXT = "Erstellt seit 2023 die konsolidierten Quartalszahlen in LucaNet mit"
RECORDED_BULLETS = [
    "Verantwortete das Teilprojekt Intercompany-Abstimmung und Kapitalkonsolidierung bei der "
    "Einführung von LucaNet in der Gruppe (GmbH + Vertriebsgesellschaft Schweiz)",
    DEPUTY_BULLET,
    "Fachliche Anleitung einer Junior-Controllerin in der Einarbeitung (Abschlussthemen, "
    "Review ihrer Reports)",
]
EMPLOYER = "Feldberg Präzision GmbH"


def _profile_json(role: str = "Financial Controller") -> dict[str, Any]:
    return {
        "personal_info": {"name": "Sabine Kessler", "email": "sk@example.com"},
        "work_experience": [{
            "id": "w-1", "company": EMPLOYER, "role": role,
            "start_date": "2019-02", "is_current": True,
            "responsibilities": [
                "Verantwortlich für Monatsabschluss und Reporting (HGB)",
                "Fachliche Anleitung von zwei Werkstudierenden",
            ],
        }],
    }


def _profile(role: str = "Financial Controller") -> MasterProfileData:
    return MasterProfileData.model_validate(_profile_json(role))


def _bullets(resp=None) -> dict[str, Any]:
    return {"op": "add_bullets", "target": "w-1",
            "responsibilities": list(RECORDED_BULLETS if resp is None else resp),
            "technologies": ["LucaNet"]}


def _run(ops, *, answer=ANSWER, source="testimony", profile=None, extra=None):
    return enforce_position_terms(
        _parse_ops(ops), profile=profile or _profile(),
        new_info={"answer": answer, **(extra or {})}, source=source,
    )


def _asks(out):
    return [o for o in out if isinstance(o, RequestConfirmation)]


# ── the true positive: the recorded turn ───────────────────────────────────

def test_recorded_turn_the_deputy_bullet_is_held_and_asked_once():
    out = _run([_bullets()])
    (op,) = [o for o in out if isinstance(o, AddBullets)]
    assert op.responsibilities == [RECORDED_BULLETS[0], RECORDED_BULLETS[2]]
    assert op.technologies == ["LucaNet"]
    (ask,) = _asks(out)
    assert ask.context["kind"] == "position_title"
    assert ask.option_keys == ["title_holds", "task_only"]
    (item,) = ask.context["items"]
    assert item["text"] == DEPUTY_BULLET and item["task_text"] == TASK_TEXT
    assert item["target"] == "w-1" and item["field"] == "responsibilities"
    de = ask.question_i18n["de"]
    assert "Stellvertreterin der Leiterin Rechnungswesen" in de and TASK_TEXT in de
    assert ask.options_i18n[0]["de"] == "Ja, das war meine Position"


def test_recorded_turn_logs_the_hold(caplog):
    caplog.set_level("INFO", logger="applire.services.profile.reconcile.position_terms")
    _run([_bullets()])
    assert "RECONCILE_POSITION_CLAIM_HELD" in caplog.text


def test_the_fixture_answer_holds_no_deputy_title():
    assert not any(f == "deputy" for f, _p in position_claims(ANSWER))
    assert [f for f, _p in position_claims(DEPUTY_BULLET)] == ["deputy"]


# ── the false-positive side: a role within a task passes ───────────────────

@pytest.mark.parametrize("bullet", [
    "Projektleitung für die Migration auf S/4HANA; Abstimmung mit der IT",
    "Verantwortlich für die Konsolidierung der Quartalszahlen",
    "Led the project to migrate the group reporting to Power BI",
    "Teilprojektleiterin Intercompany-Abstimmung bei der LucaNet-Einführung",
    "Leitung der Stammdatenbereinigung für die Arbeitspläne",
    "Leiter des IT-Projekts zur Belegerfassung",
    "Key-User für PP beim SAP-Rollout",
])
def test_a_role_within_a_task_passes(bullet):
    out = _run([_bullets(resp=[bullet])], answer="Ich habe das gemacht.")
    assert _asks(out) == []
    assert out[0].responsibilities == [bullet]


@pytest.mark.parametrize("title", [
    "Leiterin Rechnungswesen", "Abteilungsleiter Einkauf", "Head of Finance",
    "Team Lead Backend", "stellvertretende Teamleiterin", "Deputy Head of Accounting",
    "IT-Leiter", "Production Manager",
])
def test_a_title_tied_to_a_unit_is_a_position_claim(title):
    assert position_claims(title)


# ── holding: the person's words or the record they hold ────────────────────

def test_the_persons_own_deputy_title_holds_it():
    answer = ANSWER.replace("ich bin die operative Nummer zwei dafür",
                            "ich bin stellvertretende Leiterin Rechnungswesen")
    out = _run([_bullets()], answer=answer)
    assert _asks(out) == [] and out[0].responsibilities == RECORDED_BULLETS


def test_a_bare_anaphoric_deputy_word_is_asked_once():
    # MD-8 F1: held per title (title + unit). "ihre Stellvertreterin" names no
    # unit; whose deputy it is would be a reading of the previous sentence.
    answer = ANSWER.replace("ich bin die operative Nummer zwei dafür", "ich bin ihre Stellvertreterin")
    assert len(_asks(_run([_bullets()], answer=answer))) == 1


def test_the_edited_entry_holds_it():
    out = _run([_bullets()], profile=_profile(role="Stellv. Leiterin Rechnungswesen"))
    assert _asks(out) == []


def test_another_entrys_title_does_not_hold_this_entry():
    p = MasterProfileData.model_validate({"work_experience": [
        {"id": "w-1", "company": EMPLOYER, "role": "Financial Controller"},
        {"id": "w-2", "company": "Alt GmbH", "role": "Stellvertretende Leiterin Controlling"},
    ]})
    assert len(_asks(_run([_bullets()], profile=p))) == 1


def test_the_person_saying_they_lead_the_unit_holds_a_unit_head_title():
    out = _run([_bullets(resp=["Teamleiterin Controlling mit drei Mitarbeitenden"])],
               answer="Ich leite das Team Controlling mit drei Leuten.")
    assert _asks(out) == []


def test_on_the_interview_the_question_is_not_the_persons_word():
    out = _run([_bullets(resp=["Teamleiter der Disposition mit sechs Mitarbeitenden"])],
               answer="Ja, die Disposition mit sechs Leuten.", source="interview",
               extra={"gap": "Teamleitung", "question": "Waren Sie Teamleiter der Disposition?"})
    assert len(_asks(out)) == 1


@pytest.mark.parametrize("source", ["cv_upload", "manual", "cv"])
def test_a_cv_import_or_a_manual_edit_is_not_guarded(source):
    ops = _parse_ops([_bullets()])
    assert enforce_position_terms(ops, profile=_profile(), new_info={"answer": ANSWER},
                                  source=source) is ops


def test_the_agent_interview_door_is_guarded_on_the_answer():
    out = _run([_bullets(resp=["Stellvertretende Leiterin Rechnungswesen"])],
               source="agent_interview", extra={"question": "Sind Sie Stellvertreterin?"})
    assert len(_asks(out)) == 1
    assert [o.responsibilities for o in out if isinstance(o, AddBullets)] == [[]]


def test_two_held_bullets_make_one_ask():
    # An answer that names no unit head of anyone: the known limit is that the
    # boss's "Leiterin Rechnungswesen" in ANSWER holds the unit-head family.
    out = _run([_bullets(resp=[DEPUTY_BULLET, "Teamleiterin Konsolidierung"]),
                {"op": "add_bullets", "target": "w-1", "achievements": ["Head of Finance ab 2025"]}],
               answer="Ich bin die operative Nummer zwei bei der Konsolidierung.")
    (ask,) = _asks(out)
    assert len(ask.context["items"]) == 3
    assert len([o for o in out if isinstance(o, AddBullets)]) == 1


# ── roles ──────────────────────────────────────────────────────────────────

def test_a_new_stations_unit_title_is_held_and_the_station_created_without_it():
    out = _run([{"op": "upsert_work", "ref": "r1", "company": "Neu AG",
                 "role": "Abteilungsleiter Einkauf"}], answer="Bei der Neu AG war ich im Einkauf.")
    (work,) = [o for o in out if isinstance(o, UpsertWork)]
    assert work.role == "" and work.company == "Neu AG"
    (item,) = _asks(out)[0].context["items"]
    assert item["item"] == "role" and item["company"] == "Neu AG" and item["target"] is None


def test_a_change_to_the_edited_entrys_role_the_answer_lacks_is_held():
    out = _run([{"op": "set_field", "target": "w-1", "field": "role", "value": "Senior Controller"},
                {"op": "set_field", "target": "w-1", "field": "location", "value": "Freiburg"}])
    assert [o.field for o in out if isinstance(o, SetField)] == ["location"]
    assert _asks(out)[0].context["items"][0]["text"] == "Senior Controller"


def test_a_role_change_the_answer_states_passes():
    out = _run([{"op": "set_field", "target": "w-1", "field": "role", "value": "Senior Controller"}],
               answer="Seit 2024 bin ich Senior Controller.")
    assert _asks(out) == [] and isinstance(out[0], SetField)


# ── the answer: title or task wording ──────────────────────────────────────

def _parked(ask: RequestConfirmation, profile_json=None) -> MasterProfileData:
    p = MasterProfileData.model_validate(profile_json or _profile_json())
    p.metadata = ProfileMetadata(pending_confirmations=[PendingConfirmation(
        confirmation_id="c-1", question=ask.question, options=list(ask.options),
        context=dict(ask.context), question_i18n=ask.question_i18n,
        options_i18n=ask.options_i18n, option_keys=list(ask.option_keys),
    )])
    return p


def _answer(ask, chosen, profile_json=None):
    return apply_ops(_parked(ask, profile_json),
                     [ResolveConfirmation(confirmation_id="c-1", chosen_option=chosen)], "review")


def test_yes_in_german_writes_the_title():
    (ask,) = _asks(_run([_bullets()]))
    _q, shown = _parked(ask).metadata.pending_confirmations[0].rendered("de")
    out = _answer(ask, shown[0])
    assert DEPUTY_BULLET in out.profile.work_experience[0].responsibilities


def test_no_writes_the_task_wording_without_the_title():
    (ask,) = _asks(_run([_bullets()]))
    out = _answer(ask, "No, only for this task")
    resp = out.profile.work_experience[0].responsibilities
    assert TASK_TEXT in resp and DEPUTY_BULLET not in resp


def test_a_free_text_answer_that_matches_no_option_never_writes_the_title():
    # T-1 / RULING MD-5: any answer but the yes writes the task wording.
    (ask,) = _asks(_run([_bullets()]))
    out = _answer(ask, "hm, schwer zu sagen")
    resp = out.profile.work_experience[0].responsibilities
    assert TASK_TEXT in resp and DEPUTY_BULLET not in resp
    assert out.pending_confirmations == []


def test_no_on_a_bare_title_writes_nothing_and_says_so():
    (ask,) = _asks(_run([_bullets(resp=["Stellvertretende Leiterin Rechnungswesen"])]))
    out = _answer(ask, "No, only for this task")
    assert out.profile.work_experience[0].responsibilities == _profile().work_experience[0].responsibilities
    assert [n.reason for n in out.not_applied] == ["confirmation_discarded"]


def test_yes_on_a_role_change_adds_it_as_an_alias():
    (ask,) = _asks(_run([{"op": "set_field", "target": "w-1", "field": "role",
                          "value": "Senior Controller"}]))
    out = _answer(ask, "Yes, that was my position")
    w = out.profile.work_experience[0]
    assert w.role == "Financial Controller" and "Senior Controller" in w.role_aliases


def test_yes_on_a_new_stations_title_writes_its_role():
    (ask,) = _asks(_run([{"op": "upsert_work", "ref": "r1", "company": "Neu AG",
                          "role": "Abteilungsleiter Einkauf"}], answer="Bei der Neu AG war ich im Einkauf."))
    pj = _profile_json()
    pj["work_experience"].append({"id": "w-2", "company": "Neu AG", "role": ""})
    out = _answer(ask, "Yes, that was my position", pj)
    assert out.profile.work_experience[1].role == "Abteilungsleiter Einkauf"


# ── the engine seam and the delivery point ─────────────────────────────────

class _Stub:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    async def aparse_json(self, prompt: str, **kwargs: Any) -> Any:
        return self.payload


_PAYLOAD = {"ops": [_bullets()], "ambiguities": [], "denials": []}


@pytest.mark.asyncio
async def test_engine_seam_the_recorded_batch_holds_the_deputy_bullet():
    result = await reconcile(_profile(), {"answer": ANSWER}, "testimony", _Stub(_PAYLOAD))
    bullets = [o for o in result.ops if isinstance(o, AddBullets)]
    assert DEPUTY_BULLET not in bullets[0].responsibilities
    assert [o.context.get("kind") for o in result.ops if isinstance(o, RequestConfirmation)] == [
        "position_title"
    ]


@pytest_asyncio.fixture
async def db():
    import importlib
    import pkgutil

    import applire.models  # noqa: F401

    for _m in pkgutil.iter_modules(applire.models.__path__):
        importlib.import_module(f"applire.models.{_m.name}")
    from applire.db.session import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_delivery_point_agent_door_and_health_hub_show_the_ask(db):
    """What the person sees: the agent door's ``submit_testimony`` returns the ask
    (status needs_confirmation, the German question, the two options) and the
    vault does not hold the title; the web door's Health hub lists the ask."""
    from applire.mcp.server import submit_testimony
    from applire.services.profile import _get_latest
    from applire.services.profile.health import assess_health
    from tests.support.profile_factory import make_master_profile

    db.add(make_master_profile(profile_json=_profile_json()))
    await db.commit()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=db)
    cm.__aexit__ = AsyncMock(return_value=False)
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.get_provider", return_value=_Stub(_PAYLOAD)),
    ):
        payload = await submit_testimony(text=ANSWER)

    assert payload["status"] == "needs_confirmation"
    (conf,) = payload["confirmations"]
    assert "Stellvertreterin der Leiterin Rechnungswesen" in conf["question_i18n"]["de"]
    assert [o["de"] for o in conf["options_i18n"]] == [
        "Ja, das war meine Position", "Nein, nur für diese Aufgabe",
    ]
    record = await _get_latest(db, None)
    stored = MasterProfileData.model_validate(record.profile_json)
    assert DEPUTY_BULLET not in stored.work_experience[0].responsibilities
    issues = [i for i in assess_health(stored).issues if i.thread == "confirmation"]
    assert len(issues) == 1 and "Stellvertreterin" in issues[0].question_i18n["de"]


# ── MD-4: a model-emitted confirmation can never drive a family-7 write ─────

_FORGED_TEXT = "Leiterin Rechnungswesen und Prokuristin (vom Modell erfunden)"
_FORGED_CONTEXT = {
    "kind": "position_title",
    "items": [
        {"item": "bullet", "target": "w-1", "field": "responsibilities",
         "text": _FORGED_TEXT, "task_text": _FORGED_TEXT},
        {"item": "role", "target": "w-1", "field": "role", "text": _FORGED_TEXT},
    ],
    "section": "work_experience",
}
_FORGED_ASK = {"question": "Stimmt das?", "options": ["Ja", "Nein"], "context": _FORGED_CONTEXT}


def _forged_everywhere(profile: MasterProfileData) -> bool:
    return any(
        _FORGED_TEXT in (w.role or "") or _FORGED_TEXT in w.role_aliases
        or _FORGED_TEXT in w.responsibilities
        for w in profile.work_experience
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["op", "ambiguities"])
async def test_a_forged_family7_confirmation_is_stripped_and_writes_nothing(path):
    """Both raw paths (the op array and the top-level ``ambiguities``): the
    code-only context keys are stripped, and the person's "Ja" on what is left
    writes nothing — through the real engine, the park and the applier."""
    payload = {"ops": [], "ambiguities": [], "denials": []}
    if path == "op":
        payload["ops"] = [{"op": "request_confirmation", **_FORGED_ASK}]
    else:
        payload["ambiguities"] = [dict(_FORGED_ASK)]
    result = await reconcile(_profile(), {"answer": "Ich erstelle die Quartalszahlen."},
                             "testimony", _Stub(payload))
    asks = [o for o in result.ops if isinstance(o, RequestConfirmation)] + list(result.ambiguities)
    (ask,) = asks
    assert "kind" not in ask.context and "items" not in ask.context
    assert ask.context.get("section") == "work_experience"  # the rest survives
    for chosen in ("Ja", "Nein", "Ja, das war meine Position"):
        out = _answer(ask, chosen)
        assert not _forged_everywhere(out.profile), chosen
        assert out.profile.work_experience[0].responsibilities == (
            _profile().work_experience[0].responsibilities
        )


@pytest.mark.parametrize("chosen", ["Ja", "Nein", "Yes, that was my position"])
def test_a_keyless_record_with_family7_context_writes_nothing(chosen):
    """Defence in depth (MD-4 b): a record that reached the park WITH the kind
    and items but without the code-built keys is not family 7."""
    ask = RequestConfirmation(question="Stimmt das?", options=["Ja", "Nein"],
                              context=dict(_FORGED_CONTEXT))
    out = _answer(ask, chosen)
    assert not _forged_everywhere(out.profile)
    assert out.profile.work_experience[0].role == "Financial Controller"
    assert out.pending_confirmations == []  # a forged ask is not asked again


@pytest.mark.parametrize("item", [
    {"item": "bullet", "target": "w-1", "field": "technologies", "text": "Forged-Tool"},
    {"item": "bullet", "target": "w-1", "field": "role_aliases", "text": "Forged-Alias"},
    {"item": "bullet", "target": "e-1", "field": "responsibilities", "text": "Forged-Edu"},
    {"item": "bullet", "target": "v-1", "field": "responsibilities", "text": "Forged-Volunteer"},
    {"item": "role", "target": "w-1", "field": "company", "text": "Forged-Company"},
])
def test_a_yes_writes_only_the_allow_listed_fields_of_work_and_project_entries(item):
    """MD-4 (c): even a code-keyed record writes only bullets into a work or
    project entry's bullet lists, or a role."""
    (ask,) = _asks(_run([_bullets()]))
    tampered = ask.model_copy(update={"context": {**ask.context, "items": [item]}})
    pj = _profile_json()
    pj["education"] = [{"id": "e-1", "institution": "Hochschule Beispiel", "degree": "B.A."}]
    pj["volunteer_activities"] = [{"id": "v-1", "organization": "Sportverein Beispiel"}]
    out = _answer(tampered, "Yes, that was my position", pj)
    assert item["text"] not in out.profile.volunteer_activities[0].responsibilities
    w = out.profile.work_experience[0]
    assert item["text"] not in w.technologies + w.role_aliases + w.responsibilities
    assert w.company == EMPLOYER
    assert [n.reason for n in out.not_applied] == ["confirmation_unresolvable"]



_FORGED_OTHER_FAMILIES = {
    "family4_attribution": {"flagged": [{"field": "responsibilities", "text": _FORGED_TEXT}],
                            "anchor_employer": EMPLOYER, "target": "w-1",
                            "section": "work_experience"},
    "family6_project_parent": {"kind": "project_parent", "project": "Konsolidierung"},
    "denial_scope": {"kind": "denial_scope", "denial_concept": "LucaNet"},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("family", sorted(_FORGED_OTHER_FAMILIES))
@pytest.mark.parametrize("chosen", ["move", "keep_here", "entry:w-1", "cv_holds", "Ja"])
async def test_families_4_6_and_denial_scope_write_nothing_from_a_model_emitted_ask(family, chosen):
    """MD-4's check of the sibling families: they resolve only on a code-built
    option key, which the model cannot emit — so a forged context writes nothing."""
    payload = {"ops": [], "denials": [], "ambiguities": [{
        "question": "Stimmt das?", "options": ["Ja", "Nein"],
        "context": dict(_FORGED_OTHER_FAMILIES[family]),
    }]}
    result = await reconcile(_profile(), {"answer": "Ich erstelle die Quartalszahlen."},
                             "testimony", _Stub(payload))
    (ask,) = list(result.ambiguities)
    assert not ask.option_keys
    out = _answer(ask, chosen)
    assert not _forged_everywhere(out.profile)
    assert out.profile.work_experience[0].responsibilities == (
        _profile().work_experience[0].responsibilities
    )
    assert out.profile.metadata.denied_concepts == []


# ── MD-6: an engagement ask's carried content passes the same guards ─────────

_DEPUTY_ROLE = "Stellvertretende Leiterin Rechnungswesen"


def _engagement_payload() -> dict[str, Any]:
    """A model-emitted V-6 ask ("a separate position?") whose carried entity op
    and bullets bring a deputy title the answer never states."""
    return {"ops": [], "denials": [], "ambiguities": [{
        "question": "Ist das eine eigene Position?",
        "options": ["merge", "add_separate"],
        "context": {
            "section": "work_experience",
            "incoming": {"op": "upsert_work", "ref": "r9", "company": "Neuhaus Logistik AG",
                         "role": _DEPUTY_ROLE, "start_date": "2021-01"},
            "pending_bullets": {"responsibilities": [DEPUTY_BULLET, "Monatsabschluss nach HGB"]},
            "existing_ids": [],
        },
    }]}


def _texts(profile: MasterProfileData) -> list[str]:
    return [t for w in profile.work_experience
            for t in [w.role or "", *w.role_aliases, *w.responsibilities]]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["ambiguities", "op"])
async def test_a_forged_engagement_ask_is_guarded_at_park_time(path):
    payload = _engagement_payload()
    if path == "op":
        payload["ops"] = [{"op": "request_confirmation", **payload.pop("ambiguities")[0]}]
        payload["ambiguities"] = []
    result = await reconcile(_profile(), {"answer": ANSWER}, "interview", _Stub(payload))
    asks = [o for o in result.ops if isinstance(o, RequestConfirmation)] + list(result.ambiguities)
    (ask,) = [a for a in asks if "incoming" in a.context]
    result.ops = [o for o in result.ops if o is not ask]
    assert ask.context["incoming"]["role"] == ""
    assert ask.context["pending_bullets"]["responsibilities"] == ["Monatsabschluss nach HGB"]
    (held,) = [o for o in result.ops if isinstance(o, RequestConfirmation)]
    assert held.context["kind"] == "position_title" and held.option_keys == [
        "title_holds", "task_only",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("chosen", ["add_separate", "merge", "Ja"])
async def test_the_interview_door_executes_only_the_guarded_engagement_content(db, chosen):
    """The real seam: ``reconcile`` parks the ask, the interview door's
    ``_apply_engagement_confirmation`` executes its context on the answer."""
    from applire.services.profile import _get_latest
    from applire.services.session import _apply_engagement_confirmation
    from tests.support.profile_factory import make_master_profile

    db.add(make_master_profile(profile_json=_profile_json()))
    await db.commit()
    result = await reconcile(_profile(), {"answer": ANSWER}, "interview",
                             _Stub(_engagement_payload()))
    (ask,) = result.ambiguities
    record = await _get_latest(db, None)
    await _apply_engagement_confirmation(
        db, record, dict(ask.context), chosen, session_id="s-1", option_key=None,
    )
    await db.commit()
    stored = MasterProfileData.model_validate((await _get_latest(db, None)).profile_json)
    texts = _texts(stored)
    assert not any("Stellvertret" in t for t in texts), texts
    assert "Monatsabschluss nach HGB" in texts


@pytest.mark.asyncio
async def test_a_denied_technology_in_an_engagement_ask_is_dropped_by_the_stance_guard():
    payload = _engagement_payload()
    ctx = payload["ambiguities"][0]["context"]
    ctx["incoming"]["role"] = "Controllerin"
    ctx["pending_bullets"] = {"responsibilities": ["Monatsabschluss nach HGB"],
                              "technologies": ["Tableau", "LucaNet"]}
    payload["denials"] = ["Tableau"]
    result = await reconcile(_profile(), {"answer": ANSWER + "\nMit Tableau habe ich nie gearbeitet."},
                             "interview", _Stub(payload))
    (ask,) = result.ambiguities
    assert ask.context["pending_bullets"]["technologies"] == ["LucaNet"]


@pytest.mark.asyncio
async def test_another_employers_clause_in_an_engagement_ask_is_held_by_attribution():
    pj = _profile_json()
    pj["work_experience"].append({"id": "w-2", "company": "Brandel Kunststoffwerk GmbH",
                                  "role": "Bilanzbuchhalterin", "start_date": "2014-01",
                                  "end_date": "2018-12"})
    payload = _engagement_payload()
    ctx = payload["ambiguities"][0]["context"]
    ctx["incoming"].update(company=EMPLOYER, role="Controllerin")
    ctx["existing_ids"] = ["w-1"]
    other = "Bei Brandel Kunststoffwerk erstellte ich 2016 den Jahresabschluss nach HGB"
    ctx["pending_bullets"] = {"responsibilities": ["Monatsabschluss nach HGB", other]}
    answer = ("Bei Feldberg mache ich den Monatsabschluss nach HGB. Bei Brandel "
              "Kunststoffwerk erstellte ich 2016 den Jahresabschluss nach HGB.")
    result = await reconcile(MasterProfileData.model_validate(pj), {"answer": answer},
                             "interview", _Stub(payload))
    (ask,) = result.ambiguities
    carried = ask.context.get("pending_bullets") or {}
    assert other not in (carried.get("responsibilities") or [])
    # Held exactly as on the normal path: attribution's own ask (family 4).
    flagged = [f["text"] for o in result.ops if isinstance(o, RequestConfirmation)
               for f in (o.context or {}).get("flagged") or []]
    assert other in flagged


# ── MD-8 (adversarial F1–F3): per title, every field, the person's own ──────

from applire.services.profile.reconcile.position_terms import task_wording  # noqa: E402


@pytest.mark.parametrize("bullet", [
    "Erstellt seit 2023 die Quartalszahlen in LucaNet; Leiterin Rechnungswesen",
    "Erstellt seit 2023 die Quartalszahlen in LucaNet; operative Leiterin Rechnungswesen",
    "Verantwortet als Leiter Rechnungswesen die Quartalszahlen",
])
def test_f1_a_third_partys_title_in_the_answer_licenses_nothing(bullet):
    # The incident answer names "unsere Leiterin Rechnungswesen" — the boss.
    assert len(_asks(_run([_bullets(resp=[bullet])]))) == 1


@pytest.mark.parametrize("answer, bullet", [
    ("Meine Stellvertreterin ist Frau Weber, ich erstelle die Quartalszahlen.",
     "Stellvertretende Leiterin Rechnungswesen, erstellt die Quartalszahlen"),
    ("Unsere Teamleiterin Frau Weber verteilt die Aufgaben; ich erstelle die Quartalszahlen.",
     "Teamleiterin Controlling, erstellt die Quartalszahlen"),
    ("Ich war nie Abteilungsleiter, nur im Projekt Chef.", "Abteilungsleiter Controlling"),
    ("Ich leite das Projekt Controlling-Reporting.", "Leiterin Rechnungswesen"),
    ("Ich bin Teamleiterin Einkauf.", "Teamleiterin Controlling"),
    ("Ich war nie Abteilungsleiter Controlling.", "Abteilungsleiter Controlling"),
    ("Frau Weber ist Teamleiterin Controlling.", "Teamleiterin Controlling"),
    ("Ich leite das Projekt im Team Controlling.", "Teamleiterin Controlling"),
    ("I never led the controlling team.", "Teamleiterin Controlling"),
    ("Ich leite nicht das Team Controlling.", "Teamleiterin Controlling"),
    ("Das gilt vice versa, ich erstelle die Zahlen.", "Stellvertretende Teamleiterin Controlling"),
])
def test_f1_held_per_title_not_per_family(answer, bullet):
    assert len(_asks(_run([_bullets(resp=[bullet])], answer=answer))) == 1


def test_f1_the_person_naming_the_same_title_holds_it():
    out = _run([_bullets(resp=["Teamleiterin Controlling, erstellt die Quartalszahlen"])],
               answer="Ich bin Teamleiterin Controlling und erstelle die Quartalszahlen.")
    assert _asks(out) == []


_F3_PASS = [
    "Berichtete an die Abteilungsleiterin Controlling über die Abschlüsse",
    "Berichtete direkt an die Abteilungsleiterin Controlling",
    "Reported to the Head of Finance",
    "Abstimmung mit dem Head of Accounting",
    "Mentored the team lead on reporting",
    "Vertretung der Abteilungsleitung im Urlaub",
    "Vertretung im Urlaub der Teamleiterin",
    "Vertretung der Teamleiterin im Urlaub",
    "Vertrat im Urlaub die Abteilungsleiterin Controlling bei der Monatsberichterstattung",
    "Covered for the deputy head of accounting during vacation",
    "Teamlead für das Projekt Cloud-Migration",
    "Team lead for the migration project",
    "Leitete die Migration des ERP-Systems",
    "Verantwortete die Konsolidierung der Quartalszahlen",
    "Kommissarische Leitung des Projekts",
    "Projektleitung für die Migration",
]


@pytest.mark.parametrize("bullet", _F3_PASS)
def test_f3_a_third_partys_title_or_a_task_scoped_cover_is_not_held(bullet):
    out = _run([_bullets(resp=[bullet])], answer="Ich erstelle die Quartalszahlen.")
    assert _asks(out) == [] and out[0].responsibilities == [bullet]


@pytest.mark.parametrize("bullet", [
    "Kommissarische Leitung der Abteilung Finanz",
    "Stellvertr. der Leiterin Rechnungswesen",
    "Worked as a team lead for accounting",
    "Koordinierte als die zuständige Teamleiterin Controlling die Konsolidierung",
])
def test_f3_the_persons_own_title_is_still_held(bullet):
    assert len(_asks(_run([_bullets(resp=[bullet])], answer="Ich erstelle die Quartalszahlen."))) == 1


def test_f3_task_wording_drops_only_the_as_title_phrase():
    assert task_wording("Koordinierte als Teamleiterin Controlling die Konsolidierung", set()) == (
        "Koordinierte die Konsolidierung"
    )


_F2_ANSWER = "Ich bin die operative Nummer zwei bei der Konsolidierung."
_F2_TITLE = "Stellvertretende Abteilungsleiterin Rechnungswesen"


@pytest.mark.parametrize("op, field", [
    ({"op": "set_summary", "lang": "de", "text": f"{_F2_TITLE} mit 5 Jahren Erfahrung"}, "summary"),
    ({"op": "upsert_volunteer", "ref": "v", "organization": "Verein", "role": _F2_TITLE}, "role"),
    ({"op": "upsert_project", "ref": "p", "name": "X", "role": "Head of Accounting"}, "role"),
    ({"op": "upsert_project", "ref": "p", "name": "X", "description": _F2_TITLE}, "description"),
    ({"op": "set_field", "target": "w-1", "field": "description", "value": _F2_TITLE}, "description"),
    ({"op": "upsert_story", "title": "t", "challenge": f"Als {_F2_TITLE} stand ich vor",
      "mechanism": "m", "outcome": "o"}, "challenge"),
])
def test_f2_every_field_that_can_carry_a_title_is_held_and_asked(op, field):
    out = _run([op], answer=_F2_ANSWER)
    (ask,) = _asks(out)
    assert [i["field"] for i in ask.context["items"]] == [field]
    assert [i["item"] for i in ask.context["items"]] == ["text"]
    written = [o for o in out if not isinstance(o, RequestConfirmation)]
    assert not any(_F2_TITLE in str(o.model_dump()) or "Head of Accounting" in str(o.model_dump())
                   for o in written)


def test_f2_a_set_field_bullet_list_is_held_per_bullet():
    out = _run([{"op": "set_field", "target": "w-1", "field": "responsibilities",
                 "value": [_F2_TITLE, "Monatsabschluss nach HGB"]}], answer=_F2_ANSWER)
    (ask,) = _asks(out)
    (sf,) = [o for o in out if isinstance(o, SetField)]
    assert sf.value == ["Monatsabschluss nach HGB"] and ask.context["items"][0]["item"] == "bullet"


def test_f2_a_yes_on_a_held_summary_writes_nothing_and_says_so():
    (ask,) = _asks(_run([{"op": "set_summary", "lang": "de", "text": _F2_TITLE}], answer=_F2_ANSWER))
    out = _answer(ask, "Yes, that was my position")
    assert _F2_TITLE not in str(out.profile.model_dump())
    assert [n.reason for n in out.not_applied] == ["confirmation_unresolvable"]


@pytest.mark.asyncio
async def test_f2_the_engagement_guard_inherits_a_volunteer_role():
    payload = {"ops": [], "denials": [], "ambiguities": [{
        "question": "Eigene Position?", "options": ["merge", "add_separate"],
        "context": {"section": "volunteer_activities", "existing_ids": [],
                    "incoming": {"op": "upsert_volunteer", "ref": "v9",
                                 "organization": "Sportverein", "role": _F2_TITLE}},
    }]}
    result = await reconcile(_profile(), {"answer": _F2_ANSWER}, "interview", _Stub(payload))
    (ask,) = result.ambiguities
    assert ask.context["incoming"]["role"] == ""
    assert [o.context["kind"] for o in result.ops if isinstance(o, RequestConfirmation)] == [
        "position_title"
    ]


def test_f2_a_volunteer_entry_that_records_the_title_holds_it():
    p = _profile_json()
    p["volunteer_activities"] = [{"id": "v-1", "organization": "Sportverein",
                                  "role": "Abteilungsleiterin Jugend"}]
    op = {"op": "upsert_volunteer", "ref": "v", "target": "v-1", "organization": "Sportverein",
          "role": "Abteilungsleiterin Jugend", "description": "Abteilungsleiterin Jugend seit 2020"}
    out = _run([op], answer=_F2_ANSWER, profile=MasterProfileData.model_validate(p))
    assert _asks(out) == []


@pytest.mark.parametrize("chosen", ["Yes, that was my position", "No, only for this task"])
def test_f2_any_answer_on_a_held_summary_leaves_it_unwritten(chosen):
    (ask,) = _asks(_run([{"op": "set_summary", "lang": "de", "text": _F2_TITLE}], answer=_F2_ANSWER))
    out = _answer(ask, chosen)
    assert _F2_TITLE not in str(out.profile.model_dump())
    assert [n.reason for n in out.not_applied] == ["confirmation_unresolvable"]
