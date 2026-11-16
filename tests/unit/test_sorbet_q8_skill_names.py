# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""MD-Q15 (Sorbet wave 2b) — a skill an answer creates is named in the person's
own words. Inputs are the captured answers of the delivery-2a runs (synthetic
personas Daniel and Stefan) and the names Luna gave them."""
from __future__ import annotations

import pytest

from applire.schemas.profile import MasterProfileData
from applire.services.profile.reconcile.ops import UpsertSkill
from applire.services.profile.reconcile.skill_names import enforce_skill_names

DANIEL = (
    "At Finleap I ran our quarterly dependency-audit rotation and fixed two externally-reported auth "
    "issues (session fixation, IDOR on invoice PDFs). I am familiar with least-privilege IAM on AWS and "
    "have no formal security certification. I have NOT worked under a PSD2 licence or with BaFin "
    "supervision; correctness-critical financial data modelling (invoicing, reconciliation, idempotency) "
    "is my daily work, the regulated wrapper is new, the engineering discipline is not."
)
STEFAN_SAP = (
    "Beim SAP-Rollout bei Rasselstein war ich Key-User für PP, also Fertigungsaufträge und Rückmeldungen. "
    "Bei Weberit arbeite ich täglich mit PP und MM, Disposition und Bestellanforderungen für "
    "Instandhaltungsmaterial, und ich habe die Stammdatenbereinigung für die Arbeitspläne geleitet."
)
STEFAN_SAFETY = (
    "Ich bin ausgebildeter Sicherheitsbeauftragter und habe bei Weberit die Vorbereitung der "
    "ISO-45001-Zertifizierung für die Produktion begleitet: Gefährdungsbeurteilungen überarbeitet, "
    "Unterweisungssystem neu aufgesetzt."
)
MES = "Bei Weberit habe ich 2023 die Einführung eines MES-Systems als Projektleiter verantwortet."

PROFILE = MasterProfileData.model_validate({"skills": [
    {"id": "s1", "name": "SAP", "category": "technical"},
    {"id": "s2", "name": "Python", "category": "technical"},
]})


def _run(answer, *ops, source="interview"):
    return enforce_skill_names(list(ops), profile=PROFILE, new_info={"answer": answer, "gap": "x"},
                               source=source)


def _skill(name, said=None, **kw):
    return UpsertSkill(name=name, said=said, category="technical", **kw)


DANIEL_PLAIN = (
    "At Finleap I ran our quarterly dependency-audit rotation. I am familiar with least-privilege "
    "IAM on AWS. Correctness-critical financial data modelling is my daily work."
)


@pytest.mark.parametrize("name", [
    "least-privilege IAM",            # the answer's phrase
    "Financial data modelling",       # case differs
    "MES-Systeme",                    # another ending of the answer's "MES-Systems"
])
def test_a_name_that_is_a_phrase_of_the_answer_is_kept(name):
    answer = DANIEL_PLAIN if "MES" not in name else MES
    assert [o.name for o in _run(answer, _skill(name))] == [name]


@pytest.mark.parametrize("name", ["least-privilege IAM", "Financial data modelling"])
def test_a_phrase_in_a_sentence_that_also_denies_is_not_stored(name):
    """MD-Q17 cost, measured (3 of 95 replay turns): Daniel's real sentence
    "I am familiar with least-privilege IAM on AWS and have no formal security
    certification" holds a negation, so the whole sentence is doubt — no skill;
    the bullets keep it."""
    assert _run(DANIEL, _skill(name, said=name)) == []


@pytest.mark.parametrize("name", ["SAP MM", "SAP PP"])
def test_a_held_skill_plus_the_answers_word_is_kept(name):
    # the profile holds "SAP"; the answer says "PP und MM"
    assert [o.name for o in _run(STEFAN_SAP, _skill(name, said="PP und MM"))] == [name]


def test_the_rec_174_label_is_replaced_by_the_persons_phrase():
    out = _run(DANIEL, _skill("Security auditing", said="dependency-audit rotation"))
    assert [o.name for o in out] == ["dependency-audit rotation"]


def test_a_token_bag_does_not_pass():
    # "security" (his certification denial) and "audit" (dependency-audit) are
    # both in the answer, but never as one phrase: without an own phrase, no skill.
    assert _run(DANIEL, _skill("Security auditing")) == []
    assert _run(DANIEL, _skill("Security auditing", said="security auditing")) == []


def test_the_stored_phrase_is_cut_from_the_answer_in_the_persons_spelling():
    out = _run(STEFAN_SAFETY, _skill("Arbeitssicherheitsmanagement", said="ausgebildeter sicherheitsbeauftragter"))
    assert [o.name for o in out] == ["ausgebildeter Sicherheitsbeauftragter"]


@pytest.mark.parametrize("said", [
    None, "", "Arbeitssicherheitsmanagement",
    # a clause: longer than the noun-phrase cap
    "die Vorbereitung der ISO-45001-Zertifizierung für die Produktion begleitet",
])
def test_no_own_phrase_means_no_skill(said):
    assert _run(STEFAN_SAFETY, _skill("Arbeitssicherheitsmanagement", said=said)) == []


def test_a_skill_targeting_a_held_skill_is_kept():
    op = _skill("SAP ERP", target="s1", existing="SAP")
    assert _run(STEFAN_SAP, op) == [op]


def test_an_unknown_target_is_judged_like_a_new_skill():
    assert _run(STEFAN_SAP, _skill("SAP ERP", target="s99")) == []


@pytest.mark.parametrize("source", ["cv_upload", "linkedin_import", "manual_edit"])
def test_other_doors_are_untouched(source):
    op = _skill("Security auditing")
    assert _run(DANIEL, op, source=source) == [op]


@pytest.mark.parametrize("source", ["interview", "agent_interview", "testimony"])
def test_every_answer_door_is_gated(source):
    assert _run(DANIEL, _skill("Security auditing"), source=source) == []


def test_other_ops_pass_through():
    from applire.services.profile.reconcile.ops import AddBullets

    bullets = AddBullets(target="w1", responsibilities=["Ran the quarterly dependency-audit rotation."])
    assert _run(DANIEL, bullets) == [bullets]


def test_short_words_need_an_exact_match():
    # "IAM" vs "I am": a short word never matches by its ending
    assert _run("I am on AWS.", _skill("IAM")) == []
    assert _run("Least-privilege IAM on AWS.", _skill("IAM")) != []


@pytest.mark.asyncio
async def test_seam_the_reconcile_engine_applies_the_gate():
    """Seam: ``engine.reconcile`` runs the gate on an interview answer, before the
    stance guard, so the op that leaves the engine carries the person's phrase."""
    from unittest.mock import AsyncMock, MagicMock

    from applire.services.profile.reconcile.engine import reconcile

    provider = MagicMock()
    provider.aparse_json = AsyncMock(return_value={"ops": [
        {"op": "upsert_skill", "name": "Security auditing", "said": "dependency-audit rotation",
         "category": "domain"},
        {"op": "upsert_skill", "name": "Observability", "said": "observability", "category": "technical"},
    ], "ambiguities": [], "denials": []})
    profile = MasterProfileData.model_validate({"work_experience": [
        {"id": "w1", "company": "Finleap", "role": "Backend Engineer"}]})
    result = await reconcile(profile, {"gap": "Security Compliance", "question": "Have you done security auditing?",
                                       "answer": DANIEL}, "interview", provider)
    names = [o.name for o in result.ops if isinstance(o, UpsertSkill)]
    assert names == ["dependency-audit rotation"]


def test_the_gate_logs_no_skill_name_and_no_answer_words(caplog):
    """Coordinator (log hygiene): neither the model's name nor the person's
    phrase reaches the application log."""
    import logging

    with caplog.at_level(logging.INFO, logger="applire.services.profile.reconcile.skill_names"):
        _run(DANIEL, _skill("Security auditing", said="dependency-audit rotation"), _skill("Observability practices"))
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "own 3-word phrase" in text and "no skill written" in text
    for words in ("Security", "auditing", "dependency", "rotation", "Observability"):
        assert words not in text



# ── MD-Q17 / adversarial F7: the words must be a claim ──────────────────────

@pytest.mark.parametrize(("answer", "name", "said", "held"), [
    ("I never did penetration testing, that was the red team.", "penetration testing", None, ()),
    ("Penetrationstests habe ich nie gemacht.", "Penetrationstests", None, ()),
    ("Ich würde gern Kubernetes lernen.", "Kubernetes", None, ()),
    ("I would like to learn Terraform.", "Terraform", "Terraform", ()),
    ("Ich habe Lean eingeführt. Python macht mein Kollege.", "Lean-Management",
     "eingeführt Python macht", ()),                                        # said spans two sentences
    ("Mein Kollege macht MM, ich nicht.", "Python MM", None, ("Python",)),
    ("Ich habe nie mit MM gearbeitet.", "SAP MM", "MM", ("SAP",)),         # composed from a denial
    ("Ich habe auch MM betreut.", "SAP MM", None, ("SAP",)),               # composition without said
    ("Ich habe das nie gemacht.", "habe das nie", None, ()),               # no content word
    ("Ich habe das gemacht.", "Programmierung", "habe das", ()),           # said without content word
    ("Ich habe nicht Python gelernt.", "Programmierung", "nicht Python", ()),
    ("Zu OEE: da war ich nicht beteiligt.", "OEE", None, ()),
])
def test_a_denial_a_wish_or_a_non_phrase_stores_no_skill(answer, name, said, held):
    profile = MasterProfileData.model_validate(
        {"skills": [{"id": f"h{i}", "name": n, "category": "technical"} for i, n in enumerate(held)]})
    out = enforce_skill_names([_skill(name, said=said)], profile=profile,
                              new_info={"answer": answer}, source="interview")
    assert out == []


@pytest.mark.parametrize(("answer", "name", "said", "expected"), [
    ("Ich habe die SPS-Programmierung für Linie 3 übernommen.", "SPS-Programmierung", None,
     "SPS-Programmierung"),
    ("Ich habe nie Java genutzt. Python nutze ich täglich.", "Python", None, "Python"),  # other sentence
    ("Ich habe auch MM betreut.", "SAP MM", "MM betreut", "SAP MM"),                    # affirmative said
    ("Version 4.0 von Odoo eingeführt.", "Odoo", None, "Odoo"),                         # "4.0" ends no sentence
])
def test_an_affirmative_phrase_is_still_stored(answer, name, said, expected):
    out = enforce_skill_names([_skill(name, said=said)], profile=PROFILE,
                              new_info={"answer": answer}, source="interview")
    assert [o.name for o in out] == [expected]
