# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#831 (Sorbet WP-T; ADR-046 amended 2026-11-13): a reconcile paraphrase never
raises the person's position.

The recorded turn (UX audit 4, 2026-11-07, Sonnet 5.5, testimony door) with the
names swapped for invented ones: the person wrote "Verantwortlich zeichnet unsere
Leiterin Rechnungswesen — ich bin die operative Nummer zwei dafür"; the reconciler
wrote "…; operative Stellvertreterin der Leiterin Rechnungswesen, die
verantwortlich zeichnet". Replays of that turn on the incident's system prompt
gave the deputy wording 7/10 on Sonnet (report.md).
"""
from __future__ import annotations

from typing import Any

import pytest

from applire.schemas.profile import MasterProfileData
from applire.services.profile.reconcile.engine import _parse_ops, reconcile
from applire.services.profile.reconcile.ops import AddBullets, SetField, UpsertStory, UpsertWork
from applire.services.profile.reconcile.position_terms import (
    enforce_position_terms,
    position_terms,
)

# The recorded answer's position-relevant sections, names invented.
ANSWER = (
    "## Konsolidierung / LucaNet\n"
    "Bei der Einführung von LucaNet in unserer kleinen Gruppe (GmbH + Vertriebs-\n"
    "gesellschaft Schweiz) habe ich das Teilprojekt Intercompany-Abstimmung und\n"
    "Kapitalkonsolidierung verantwortet und erstelle seit 2023 die konsolidierten\n"
    "Quartalszahlen darin mit. Verantwortlich zeichnet unsere Leiterin Rechnungswesen —\n"
    "ich bin die operative Nummer zwei dafür.\n\n"
    "## Führung\n"
    "Fachlich führe ich seit zwei Jahren die beiden Werkstudierenden und seit letztem\n"
    "Jahr zusätzlich eine Junior-Controllerin in der Einarbeitung (Abschlussthemen,\n"
    "Review ihrer Reports). Disziplinarische Führung hatte ich noch nicht.\n\n"
    "## Projektcontrolling / POC\n"
    "Für unseren Sondermaschinenbereich habe ich 2022 die Bewertung langfristiger\n"
    "Aufträge nach Fertigstellungsgrad eingeführt: Meilensteinlogik mit der\n"
    "Projektleitung definiert, monatliche POC-Bewertung für sechs Großprojekte.\n"
)
# The recorded op batch's add_bullets, verbatim but for the employer.
RECORDED_BULLETS = [
    "Verantwortete das Teilprojekt Intercompany-Abstimmung und Kapitalkonsolidierung bei der "
    "Einführung von LucaNet in der Gruppe (GmbH + Vertriebsgesellschaft Schweiz)",
    "Erstellt seit 2023 die konsolidierten Quartalszahlen in LucaNet mit; operative "
    "Stellvertreterin der Leiterin Rechnungswesen, die verantwortlich zeichnet",
    "Fachliche Anleitung einer Junior-Controllerin in der Einarbeitung (Abschlussthemen, "
    "Review ihrer Reports)",
]
RECORDED_ACHIEVEMENT = (
    "Führte 2022 für den Sondermaschinenbereich die Bewertung langfristiger Aufträge nach "
    "Fertigstellungsgrad ein: Meilensteinlogik mit der Projektleitung definiert"
)


def _profile(role: str = "Financial Controller", resp: tuple[str, ...] = ()) -> MasterProfileData:
    return MasterProfileData.model_validate({
        "personal_info": {"name": "Sabine Kessler", "email": "sk@example.com"},
        "work_experience": [{
            "id": "w-1", "company": "Feldberg Präzision GmbH", "role": role,
            "start_date": "2019-02", "is_current": True,
            "responsibilities": list(resp) or [
                "Verantwortlich für Monatsabschluss und Reporting (HGB)",
                "Fachliche Anleitung von zwei Werkstudierenden",
            ],
        }],
    })


def _bullets(target: str = "w-1", resp=None, ach=None) -> dict[str, Any]:
    return {"op": "add_bullets", "target": target,
            "responsibilities": list(RECORDED_BULLETS if resp is None else resp),
            "achievements": [RECORDED_ACHIEVEMENT] if ach is None else list(ach),
            "technologies": ["LucaNet"]}


def _run(ops, *, answer=ANSWER, source="testimony", profile=None, extra=None):
    new_info = {"answer": answer, **(extra or {})}
    return enforce_position_terms(
        _parse_ops(ops), profile=profile or _profile(), new_info=new_info, source=source,
    )


# ── the recorded turn ──────────────────────────────────────────────────────

def test_recorded_turn_the_deputy_clause_is_not_written_the_rest_is():
    (op,) = _run([_bullets()])
    assert isinstance(op, AddBullets)
    assert op.responsibilities == [
        RECORDED_BULLETS[0],
        "Erstellt seit 2023 die konsolidierten Quartalszahlen in LucaNet mit",
        RECORDED_BULLETS[2],
    ]
    assert op.achievements == [RECORDED_ACHIEVEMENT]
    assert op.technologies == ["LucaNet"]


def test_recorded_turn_logs_the_cut(caplog):
    caplog.set_level("INFO", logger="applire.services.profile.reconcile.position_terms")
    _run([_bullets()])
    assert "RECONCILE_POSITION_TERM_CUT" in caplog.text and "stellvertreterin" in caplog.text


def test_the_fixture_answer_really_lacks_the_deputy_family():
    assert "deputy" not in {f for f, _t in position_terms(ANSWER)}
    assert "deputy" in {f for f, _t in position_terms(RECORDED_BULLETS[1])}


# ── backing: the person's words or the record they hold ────────────────────

def test_the_persons_own_deputy_word_backs_it():
    answer = ANSWER.replace("ich bin die operative Nummer zwei dafür",
                            "ich bin ihre Stellvertreterin")
    (op,) = _run([_bullets()], answer=answer)
    assert op.responsibilities == RECORDED_BULLETS


def test_the_edited_entry_backs_it():
    (op,) = _run([_bullets()], profile=_profile(role="Stellv. Leiterin Rechnungswesen"))
    assert op.responsibilities == RECORDED_BULLETS


def test_another_entrys_title_does_not_back_this_entry():
    p = MasterProfileData.model_validate({
        "work_experience": [
            {"id": "w-1", "company": "Feldberg Präzision GmbH", "role": "Financial Controller"},
            {"id": "w-2", "company": "Alt GmbH", "role": "Stellvertretende Leiterin Controlling"},
        ],
    })
    (op,) = _run([_bullets()], profile=p)
    assert "Stellvertreterin" not in " ".join(op.responsibilities)


def test_on_the_interview_the_question_is_not_the_persons_word():
    (op,) = _run(
        [_bullets(resp=["Teamleiter der Disposition mit sechs Mitarbeitenden"], ach=[])],
        answer="Ja, die Disposition mit sechs Leuten.", source="interview",
        extra={"gap": "Teamleitung", "question": "Haben Sie ein Team geleitet?"},
    )
    assert op.responsibilities == [] and op.technologies == ["LucaNet"]


def test_the_cv_import_is_not_guarded():
    ops = _parse_ops([_bullets()])
    assert enforce_position_terms(
        ops, profile=_profile(), new_info="CV text", source="cv_upload"
    ) is ops


# ── what a hit removes ─────────────────────────────────────────────────────

def test_a_bullet_whose_only_clause_carries_the_term_is_dropped_and_an_empty_op_too():
    out = _run([{"op": "add_bullets", "target": "w-1",
                 "responsibilities": ["Operative Stellvertreterin der Leiterin Rechnungswesen"]}])
    assert out == []


def test_a_sentence_and_a_dash_are_clause_boundaries():
    (op,) = _run([_bullets(resp=[
        "Erstellt die Quartalszahlen. Vertritt die Leiterin im Urlaub.",
        "Konsolidierung in LucaNet — als Deputy Head of Accounting",
    ], ach=[])])
    assert op.responsibilities == ["Erstellt die Quartalszahlen.", "Konsolidierung in LucaNet"]


def test_an_unbacked_title_on_upsert_work_is_not_written():
    (op,) = _run([{"op": "upsert_work", "ref": "r1", "company": "Neu AG",
                   "role": "Stellvertretende Leiterin Rechnungswesen"}])
    assert isinstance(op, UpsertWork) and op.role == "" and op.company == "Neu AG"


def test_an_unbacked_title_via_set_field_is_not_written():
    out = _run([{"op": "set_field", "target": "w-1", "field": "role",
                 "value": "Deputy Head of Accounting"},
                {"op": "set_field", "target": "w-1", "field": "location", "value": "Freiburg"}])
    assert [o.field for o in out if isinstance(o, SetField)] == ["location"]


def test_a_story_keeps_its_clean_clauses_and_is_dropped_when_a_required_field_empties():
    kept = _run([{"op": "upsert_story", "title": "POC", "challenge": "Aufträge ohne POC",
                  "mechanism": "Meilensteinlogik eingeführt; als Stellvertreterin freigegeben",
                  "outcome": "Sechs Großprojekte monatlich bewertet"}])
    assert isinstance(kept[0], UpsertStory)
    assert kept[0].mechanism == "Meilensteinlogik eingeführt"
    dropped = _run([{"op": "upsert_story", "title": "POC", "challenge": "Aufträge ohne POC",
                     "mechanism": "Als Stellvertreterin freigegeben",
                     "outcome": "Sechs Großprojekte monatlich bewertet"}])
    assert dropped == []


# ── words that are no position ─────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Lead time von 12 auf 8 Tage gesenkt",
    "Abstimmung mit dem head office in Zürich",
    "Kennzahlen aus SAP abgeleitet; Audits begleitet",
    "Weiterleitung der Reports an die Geschäftsführung",
    "Verantwortlich für den Monatsabschluss",
])
def test_no_position_term(text):
    assert position_terms(text) == []


# ── the engine seam ────────────────────────────────────────────────────────

class _Stub:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    async def aparse_json(self, prompt: str, **kwargs: Any) -> Any:
        return self.payload


@pytest.mark.asyncio
async def test_engine_seam_the_recorded_batch_lands_without_the_deputy_clause():
    result = await reconcile(
        _profile(), {"answer": ANSWER}, "testimony",
        _Stub({"ops": [_bullets()], "ambiguities": [], "denials": []}),
    )
    bullets = [o for o in result.ops if isinstance(o, AddBullets)]
    assert bullets and bullets[0].responsibilities[1] == (
        "Erstellt seit 2023 die konsolidierten Quartalszahlen in LucaNet mit"
    )
