# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#831 (Sorbet Q1 cluster d; collector line #832:217, adversarial F7 of PR #841):
the position-check vocabulary.

Added to the closed list: Schichtführer/in, Gruppenführer/in, Oberarzt/Oberärztin,
Chefarzt/Chefärztin, Geschäftsführer/in, Prokurist/in; "Team Leiter(in)" as two
words; kommissarisch / interim / acting / "i.V." before a head title; "Head of
Dept(.)", "Dept Head", "Abt.-Leiter", "Abteilungsltr.". The check stays NARROW
(founder ruling T-1): a role within a task, a third party's title, a negation and
a plural clause subject stay unasked. Everything here goes through the real seam:
a stub provider forges the op batch, the real ``reconcile()`` runs the guard, the
real ``apply_ops`` parks and resolves the ask.
"""
from __future__ import annotations

from typing import Any

import pytest

from applire.schemas.profile import MasterProfileData, PendingConfirmation, ProfileMetadata
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.engine import reconcile
from applire.services.profile.reconcile.ops import (
    AddBullets,
    RequestConfirmation,
    ResolveConfirmation,
    SetField,
    UpsertWork,
)
from applire.services.profile.reconcile.position_terms import position_claims

TASK = "Erstellt die Quartalszahlen in LucaNet"
ANSWER = "Ich erstelle die Quartalszahlen in LucaNet."


EMPLOYER = "Feldberg Präzision GmbH"


def _profile_json(role: str = "Financial Controller") -> dict[str, Any]:
    return {
        "personal_info": {"name": "Sabine Kessler", "email": "sk@example.com"},
        "work_experience": [{
            "id": "w-1", "company": EMPLOYER, "role": role,
            "start_date": "2019-02", "is_current": True,
            "responsibilities": ["Verantwortlich für Monatsabschluss und Reporting (HGB)"],
        }],
    }


def _profile(role: str = "Financial Controller") -> MasterProfileData:
    return MasterProfileData.model_validate(_profile_json(role))


def _asks(out):
    return [o for o in out if isinstance(o, RequestConfirmation)]


def _answer(ask, chosen, profile_json=None):
    """Park the ask on the vault and resolve it with the person's answer."""
    p = MasterProfileData.model_validate(profile_json or _profile_json())
    p.metadata = ProfileMetadata(pending_confirmations=[PendingConfirmation(
        confirmation_id="c-1", question=ask.question, options=list(ask.options),
        context=dict(ask.context), question_i18n=ask.question_i18n,
        options_i18n=ask.options_i18n, option_keys=list(ask.option_keys),
    )])
    return apply_ops(p, [ResolveConfirmation(confirmation_id="c-1", chosen_option=chosen)], "review")


class _Stub:
    def __init__(self, ops: list[dict[str, Any]]) -> None:
        self.payload = {"ops": ops, "ambiguities": [], "denials": []}

    async def aparse_json(self, prompt: str, **kwargs: Any) -> Any:
        return self.payload


async def _reconcile(ops, *, answer=ANSWER, profile=None):
    """The real engine seam at the interview door; the question names the title
    (Applire's own words are no evidence, ADR-061/048)."""
    return await reconcile(
        profile or _profile(),
        {"answer": answer, "gap": "Leitungsfunktion", "question": "Waren Sie Schichtführerin?"},
        "interview", _Stub(ops),
    )


def _bullets_op(*bullets: str) -> dict[str, Any]:
    return {"op": "add_bullets", "target": "w-1", "responsibilities": list(bullets)}


# title as written in the bullet -> a phrase the held ask must show
_CLAIMS = [
    "Schichtführerin in der Blechumformung",
    "Schichtführer Spätschicht",
    "Gruppenführerin Montage",
    "Oberarzt der Chirurgie",
    "Oberärztin Kardiologie",
    "Chefarzt Innere Medizin",
    "leitende Oberärztin",
    "Geschäftsführer der Feldberg GmbH",
    "Geschäftsführerin",
    "Prokurist",
    "Prokuristin mit Gesamtprokura",
    "Team Leiterin Backend",
    "Team Leiter Einkauf",
    "Team-Leiterin Controlling",
    "kommissarischer Leiter",
    "Kommissarische Leiterin der Abteilung Finanz",
    "Interim Head",
    "Acting Manager",
    "acting head of finance",
    "i.V. Abteilungsleiter",
    "i.V. Leiter",
    "Department Head",
    "Dept Head",
    "Finance Head",
    "Head of Dept",
    "Head of Dept.",
    "Head of Department",
    "Abt.-Leiter",
    "Abteilungsltr.",
]


@pytest.mark.parametrize("title", _CLAIMS)
def test_each_new_title_is_a_position_claim(title):
    assert position_claims(title)


@pytest.mark.parametrize("title", _CLAIMS)
@pytest.mark.asyncio
async def test_add_bullets_path_holds_and_asks_once(title):
    bullet = f"{TASK}; {title}"
    result = await _reconcile([_bullets_op("Pflegt die Stammdaten", bullet)])
    (op,) = [o for o in result.ops if isinstance(o, AddBullets)]
    assert op.responsibilities == ["Pflegt die Stammdaten"]  # the claim left the op
    (ask,) = _asks(result.ops)
    assert ask.context["kind"] == "position_title"
    assert ask.option_keys == ["title_holds", "task_only"]
    (item,) = ask.context["items"]
    assert item["text"] == bullet and item["task_text"] == TASK


@pytest.mark.parametrize("title", ["Schichtführerin in der Blechumformung", "Oberärztin Kardiologie",
                                   "Prokurist", "Interim Head", "Team Leiterin Backend"])
@pytest.mark.asyncio
async def test_the_answer_decides_task_wording_or_title(title):
    bullet = f"{TASK}; {title}"
    result = await _reconcile([_bullets_op(bullet)])
    (ask,) = _asks(result.ops)
    # before the answer the vault holds no title
    assert not any(title in b for b in _profile().work_experience[0].responsibilities)
    no = _answer(ask, "Nein, nur für diese Aufgabe").profile.work_experience[0].responsibilities
    assert TASK in no and not any(title in b for b in no)
    yes = _answer(ask, "Ja, das war meine Position").profile.work_experience[0].responsibilities
    assert bullet in yes


@pytest.mark.parametrize("title", ["Geschäftsführer", "Schichtführerin in der Blechumformung",
                                   "kommissarischer Leiter", "Head of Dept."])
@pytest.mark.parametrize("make_op", [
    lambda t: {"op": "upsert_work", "ref": "r1", "company": "Neu AG", "role": t},
    lambda t: {"op": "set_field", "target": "w-1", "field": "role", "value": t},
])
@pytest.mark.asyncio
async def test_role_change_path_holds_and_asks(title, make_op):
    result = await _reconcile([make_op(title)], answer="Bei der Neu AG war ich im Betrieb.")
    (ask,) = _asks(result.ops)
    assert ask.option_keys == ["title_holds", "task_only"]
    (item,) = ask.context["items"]
    assert item["item"] == "role" and item["text"] == title
    assert not [o for o in result.ops if isinstance(o, SetField) and o.field == "role"]
    assert all(o.role == "" for o in result.ops if isinstance(o, UpsertWork))
    # "no": the title is never written; "yes": it is
    pj = _profile_json()
    pj["work_experience"].append({"id": "w-2", "company": "Neu AG", "role": ""})
    is_upsert = isinstance(result.ops[0], UpsertWork)
    no = _answer(ask, "Nein, nur für diese Aufgabe", pj).profile
    assert title not in [w.role for w in no.work_experience] + [a for w in no.work_experience for a in w.role_aliases]
    yes = _answer(ask, "Ja, das war meine Position", pj).profile
    written = [w.role for w in yes.work_experience] + [a for w in yes.work_experience for a in w.role_aliases]
    assert title in written, is_upsert


# ── what stays unasked (T-1: narrow) ───────────────────────────────────────

_UNASKED = [
    "Projektleitung für die Migration",
    "Teilprojektleiterin Intercompany-Abstimmung",
    "Leiter des IT-Projekts zur Belegerfassung",
    "Led the project to migrate the group reporting",
    "Key-User für PP beim SAP-Rollout",
    "Berichtete an den Geschäftsführer über die Abschlüsse",
    "Abstimmung mit der Oberärztin zur Dienstplanung",
    "Abstimmung mit Geschäftsführer und Betriebsleiter",
    "Reported to the acting head of finance",
    "Reported directly to Head of Finance",
    "Schichtführer steuern damit ihre Schicht",
    "Kommissarische Leitung des Projekts",
    "Acting manager for the migration project",
    "Interim Management für das Rollout-Programm",
    "Termine des Geschäftsführers koordiniert",
    "Geschäftsführer-Assistenz im Sekretariat",
    "Gruppenführer-Schulung für die Werkfeuerwehr organisiert",
    "Monatlicher Finance head count Bericht",
    "Sales head to head comparison of the tools",
    "i.V. der Leiterin Rechnungswesen die Abschlüsse vorbereitet",
    # sweep (560 captured calls): "Geschäftsführung" is the body, not a title.
    "Industrie-4.0-Roadmap gemeinsam mit der Geschäftsführung erarbeitet",
    "Monatliche Abweichungsanalyse mit Kommentierung für Geschäftsführung und Beirat",
]


@pytest.mark.parametrize("bullet", _UNASKED)
@pytest.mark.asyncio
async def test_a_task_role_a_third_party_or_a_plural_subject_stays_unasked(bullet):
    result = await _reconcile([_bullets_op(bullet)])
    assert _asks(result.ops) == []
    (op,) = [o for o in result.ops if isinstance(o, AddBullets)]
    assert op.responsibilities == [bullet]


@pytest.mark.asyncio
async def test_a_negated_title_stays_unasked():
    bullet = "Ich war nie Geschäftsführer"
    result = await _reconcile([_bullets_op(bullet)])
    assert _asks(result.ops) == []
    assert [o.responsibilities for o in result.ops if isinstance(o, AddBullets)] == [[bullet]]


@pytest.mark.parametrize("answer, bullet", [
    ("Ich bin Schichtführerin in der Blechumformung.", "Schichtführerin in der Blechumformung"),
    ("Ich bin Schichtführer.", "Schichtführerin"),
    ("Ich bin leitende Oberärztin.", "Oberärztin Kardiologie"),
    ("Ich bin Prokuristin.", "Prokurist"),
    ("Ich bin Geschäftsführer.", "Geschäftsführerin der Neu AG"),
    ("Ich bin kommissarischer Leiter.", "Kommissarische Leiterin"),
    ("I am acting head.", "Acting Head"),
    ("Ich bin Team Leiterin.", "Teamleiterin"),
    ("I am Head of Department.", "Head of Dept."),
])
@pytest.mark.asyncio
async def test_an_answer_that_holds_the_title_releases_it(answer, bullet):
    result = await _reconcile([_bullets_op(bullet)], answer=answer)
    assert _asks(result.ops) == []
    assert [o.responsibilities for o in result.ops if isinstance(o, AddBullets)] == [[bullet]]


@pytest.mark.parametrize("answer, bullet", [
    ("Ich bin Schichtführerin.", "Geschäftsführerin"),
    ("Ich bin Prokurist.", "Oberarzt"),
    ("Ich bin Gruppenführer.", "Schichtführer"),
])
@pytest.mark.asyncio
async def test_one_title_never_holds_another(answer, bullet):
    assert len(_asks((await _reconcile([_bullets_op(bullet)], answer=answer)).ops)) == 1


@pytest.mark.asyncio
async def test_the_edited_entry_holding_the_title_releases_it():
    p = _profile(role="Schichtführerin in der Blechumformung")
    result = await _reconcile([_bullets_op("Schichtführerin in der Blechumformung")], profile=p)
    assert _asks(result.ops) == []


def test_a_deputy_title_does_not_swallow_an_office_title_elsewhere_but_absorbs_its_own():
    fams = [f for f, _p in position_claims("Stellvertretender Geschäftsführer, Prokuristin")]
    assert sorted(fams) == ["deputy", "office:prokurist"]
    # the office title inside the deputy title is not a second claim
    assert [f for f, _p in position_claims("Stellvertretender Geschäftsführer")] == ["deputy"]


@pytest.mark.parametrize("src", ["cv_upload", "manual"])
def test_a_cv_import_or_a_manual_edit_is_still_not_guarded(src):
    from applire.services.profile.reconcile.engine import _parse_ops
    from applire.services.profile.reconcile.position_terms import enforce_position_terms
    ops = _parse_ops([_bullets_op("Prokurist")])
    assert enforce_position_terms(ops, profile=MasterProfileData.model_validate(_profile_json()),
                                  new_info={"answer": ANSWER}, source=src) is ops


def _is_ask(o: Any) -> bool:
    return isinstance(o, RequestConfirmation)


@pytest.mark.asyncio
async def test_an_abbreviation_dot_is_no_sentence_end_in_the_task_wording():
    # "i.V." / "Stellv." must not leave a stub behind when the clause is cut.
    for title in ("i.V. Abteilungsleiter", "Stellv. Leiterin Rechnungswesen"):
        result = await _reconcile([_bullets_op("Pflegt die Stammdaten", f"{TASK}; {title}")])
        (item,) = _asks(result.ops)[0].context["items"]
        assert item["task_text"] == TASK
