# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-100 M-3 — the Truthfulness Oracle reads the keyword ledger's verified backing.

The 11-04 E2E run (synthetic LIMS twin, German vault, English posting and
documents) delivered a CV whose five interview-answered claims were all
``unverifiable / noCloseEvidence`` and a letter with ``unverifiable_dominated:
true``: the deterministic grounding cannot bridge an English sentence and a
German bullet, and the cross-language judge only ever ran for skill chips. The
ledger had already recorded, per requirement, the German vault sentence that
backs it (``backing.quote``, verified by ``quote_in_vault``).

Founder ruling R-4 (option B): the quote becomes the FIRST evidence candidate of
the equivalence judge, which still decides whether THIS sentence is supported
(level, figures, employer) — never a direct verdict for a sentence. A skill chip
asks the ledger's own question ("does the candidate have X") and reads the row
(ruling WP1-1, the precedent being ``ground_skill_claim``'s ledger arm).

The fixture is a synthetic laboratory-IT case written for this test (German
vault, English claims); no name, figure or sentence comes from a real person.
"""
from __future__ import annotations

import copy
import re

import pytest

from applire.prompts.oracle_judgement import ORACLE_JUDGEMENT_SYSTEM_PROMPT
from applire.schemas.oracle import Claim
from applire.services.oracle.audit import audit_document, verify_claim

_KESTREL = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
_MOEWE = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"

Q_LIMS = "Anbindung des Probenannahme-LIMS an das ERP-System über eine zentrale Schnittstellenplattform."
Q_MES = "Kopplung des Fertigungsleitsystems an die Laborsysteme zur automatischen Übergabe der Chargendaten."
Q_TEST = "Aufbau automatisierter End-to-End-Tests für alle Systemkopplungen vor jedem Release."
Q_DATA = "Entwicklung von Datenstrecken zwischen Lagerverwaltung und Buchhaltung."
Q_AIM = "Einführung eines unternehmensweiten Integrationsgremiums."

VAULT = {
    "personal_info": {"name": "Jorinde Marlow"},
    "professional_summary": {"de": "Leiterin Labor-IT mit Schwerpunkt Systemintegration."},
    "work_experience": [
        {
            "id": _KESTREL, "company": "Kestrel Labortechnik GmbH", "role": "Leiterin Labor-IT",
            "start_date": "2019-04", "is_current": True,
            "responsibilities": [Q_LIMS, Q_MES, Q_TEST],
            "not_yet_reached": [Q_AIM],
        },
        {
            "id": _MOEWE, "company": "Möwenhafen Spedition AG", "role": "Entwickler Logistiksysteme",
            "start_date": "2014-09", "end_date": "2019-03",
            "responsibilities": [Q_DATA],
        },
    ],
    "skills": [{"name": "Schnittstellenarchitektur", "status": "confirmed"}],
    "metadata": {
        "denied_concepts": [
            {"concept": "Kubernetes", "statement": "Mit Kubernetes habe ich nicht gearbeitet.",
             "source": "interview", "date": "2026-11-04", "denial_level": "direct"},
        ],
    },
}


def _row(concept, forms, quote, path, **over):
    row = {
        "concept": concept, "surface_forms": forms, "sources": ["required"], "fit_weight": 1.0,
        "status": "direct", "claimable": True, "evidence": f"The profile shows {concept}.",
        "backing": {"quote": quote, "unit_path": path, "instrument": "quote"},
    }
    row.update(over)
    return row


LEDGER = [
    _row("LIMS", ["LIMS integration", "LIMS"], Q_LIMS, "work_experience[0].responsibilities[0]"),
    _row("MES", ["MES integration", "MES"], Q_MES, "work_experience[0].responsibilities[1]"),
    _row("Integration testing", ["Integration testing"], Q_TEST, "work_experience[0].responsibilities[2]"),
    {"concept": "Kubernetes", "surface_forms": ["Kubernetes"], "sources": ["required"], "fit_weight": 1.0,
     "status": "denied", "claimable": False, "evidence": "denied"},
]


class _Judge:
    """The equivalence judge, scripted. ``answer`` is ``True``/``False``/
    ``"uncertain"``; ``quote`` is ``"first"`` (the first evidence line, verbatim),
    or a literal string. Records every batch prompt."""

    def __init__(self, answer=True, quote="first"):
        self.answer, self.quote = answer, quote
        self.batches: list[str] = []

    async def aparse_json(self, prompt, system=None, **kw):
        if system != ORACLE_JUDGEMENT_SYSTEM_PROMPT:
            raise RuntimeError("only the judge is scripted")
        self.batches.append(prompt)
        items = []
        for block in re.split(r"\n\n(?=ITEM \d+)", prompt):
            m = re.match(r"ITEM (\d+)", block)
            if not m:
                continue
            first = re.search(r"^  \[0\] (.*)$", block, re.MULTILINE)
            quote = (first.group(1) if first else "") if self.quote == "first" else self.quote
            items.append({"index": int(m.group(1)), "corresponds": self.answer, "vault_quote": quote})
        return {"items": items}


def _cv(bullets_0=(), bullets_1=(), summary="", skills=()):
    return {
        "summary": summary,
        "work_history": [
            {"id": _KESTREL, "company": "Kestrel Labortechnik GmbH", "title": "Head of Laboratory IT",
             "start_date": "2019-04", "end_date": None, "bullets": list(bullets_0)},
            {"id": _MOEWE, "company": "Möwenhafen Spedition AG", "title": "Logistics Systems Developer",
             "start_date": "2014-09", "end_date": "2019-03", "bullets": list(bullets_1)},
        ],
        "skills": list(skills),
        "education": [], "languages": [], "projects": [], "certifications": [],
    }


def _by_loc(report):
    return {c.claim.location: c.verdict for c in report.claims}


MES_BULLET = "Connected the plant's manufacturing execution system (MES) with the laboratory systems to transfer batch data automatically."


# ── the reader: ledger_backings ──────────────────────────────────────────────


def _backed_concepts(ledger, profile=VAULT):
    from applire.services.oracle.audit import _claimable_index
    from applire.services.oracle.backing import ledger_backings

    return [b.concept for b in ledger_backings(ledger, profile, _claimable_index(profile))]


def test_reader_returns_rows_whose_quote_verifies_now():
    assert _backed_concepts(LEDGER) == ["LIMS", "MES", "Integration testing"]


def test_reader_drops_a_row_whose_bullet_was_edited():
    """The vault moved on since the analysis: the stale quote backs nothing."""
    edited = copy.deepcopy(VAULT)
    edited["work_experience"][0]["responsibilities"][1] = "Fertigungsleitsystem betreut."
    assert "MES" not in _backed_concepts(LEDGER, edited)


@pytest.mark.parametrize(
    "over",
    [
        {"claimable": False, "status": "gap"},
        {"backing": None},
        {"backing": {"quote": "Fertigungsleitsystem"}},  # one word of a sentence: not a quote (cl. 3c)
        {"adjacent_evidence": "Schnittstellenarchitektur", "status": "partial"},  # positioning-only
        {"bar": {"kind": "team_size", "value": 10}},  # scope row
        {"aim_only": True},
    ],
    ids=["unclaimable", "no-backing", "fragment", "positioning-only", "scope", "aim-only"],
)
def test_reader_excludes_rows_the_old_literal_miss_also_refused(over):
    row = _row("MES", ["MES integration", "MES"], Q_MES, "work_experience[0].responsibilities[1]", **over)
    assert _backed_concepts([row]) == []


def test_reader_excludes_a_quote_in_an_aim_line():
    """D-12: a `not_yet_reached` line backs nothing, wherever its words stand."""
    row = _row("Integration governance", ["Integration governance"], Q_AIM, "work_experience[0].not_yet_reached[0]")
    assert _backed_concepts([row]) == []


def test_reader_excludes_an_aim_the_profile_repeats_elsewhere():
    """D-12 "wherever else the profile repeats its words": the aim line is not a
    unit, but a project description repeating it is — the quote still backs nothing."""
    vault = copy.deepcopy(VAULT)
    vault["projects"] = [{"name": "Gremium", "description": "Geplant: " + Q_AIM}]
    row = _row("Integration governance", ["Integration governance"], Q_AIM, "projects[0].description")
    assert _backed_concepts([row], vault) == []


def test_reader_excludes_a_quote_in_an_unconfirmed_entry():
    vault = copy.deepcopy(VAULT)
    vault["skills"].append({"name": "Projektleitung Laborsysteme", "status": "unconfirmed"})
    row = _row("Project management", ["Project management"], "Projektleitung Laborsysteme", "skills[1]")
    assert _backed_concepts([row], vault) == []


def test_reader_excludes_a_row_a_recorded_denial_covers():
    """cl. 7: a later denial outranks a quote — even when the row still says `direct`."""
    row = _row("Kubernetes", ["Kubernetes"], Q_TEST, "work_experience[0].responsibilities[2]")
    assert _backed_concepts([row]) == []


def test_reader_tolerates_junk():
    assert _backed_concepts([None, "x", {"concept": "A", "claimable": True, "status": "direct", "backing": "q"}]) == []
    assert _backed_concepts(None) == []


# ── sentences: the quote is the judge's FIRST evidence candidate (R-4) ──────


@pytest.mark.asyncio
async def test_sentence_naming_a_backed_row_is_judged_with_the_quote_first():
    judge = _Judge(answer=True)
    report = await audit_document(
        "cv", VAULT, tailored_data=_cv(bullets_0=[MES_BULLET]), provider=judge,
        document_language="en", keyword_ledger=LEDGER,
    )
    v = _by_loc(report)["work_history[0].bullets[0]"]
    assert v.verdict == "grounded", v
    assert v.checker == "backing_judgement"
    assert v.detail_key == "backingEquivalent"
    assert [e.ref for e in v.evidence if e.kind == "profile_path"] == ["work_experience[0].responsibilities[1]"]
    assert len(judge.batches) == 1
    item = judge.batches[0].split("ITEM 0 (mode: backing):", 1)[1]
    assert item.split("[0] ", 1)[1].startswith(Q_MES), item  # the quote leads the evidence
    assert "Kestrel Labortechnik GmbH" in item  # the employer half is judgeable


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [False, "uncertain"])
async def test_a_judge_no_keeps_the_pre_existing_verdict(answer):
    report = await audit_document(
        "cv", VAULT, tailored_data=_cv(bullets_0=[MES_BULLET]), provider=_Judge(answer=answer),
        document_language="en", keyword_ledger=LEDGER,
    )
    v = _by_loc(report)["work_history[0].bullets[0]"]
    assert (v.verdict, v.checker, v.detail_key) == ("unverifiable", "grounding", "noCloseEvidence")
    assert report.judgement_unavailable == 0


@pytest.mark.asyncio
async def test_a_quote_outside_the_offered_units_never_grants():
    judge = _Judge(answer=True, quote="Leitung eines Integrationsprogramms mit 40 Personen.")
    report = await audit_document(
        "cv", VAULT, tailored_data=_cv(bullets_0=[MES_BULLET]), provider=judge,
        document_language="en", keyword_ledger=LEDGER,
    )
    v = _by_loc(report)["work_history[0].bullets[0]"]
    assert (v.verdict, v.detail_key) == ("unverifiable", "noCloseEvidence")
    assert report.judgement_unavailable == 1


@pytest.mark.asyncio
async def test_no_provider_report_is_byte_identical_to_the_ledger_less_report():
    """Fail-safe: without a judge the seam opens no candidate and counts nothing."""
    cv = _cv(bullets_0=[MES_BULLET], summary="Laboratory IT lead for LIMS integration.")
    with_ledger = await audit_document("cv", VAULT, tailored_data=cv, provider=None,
                                       document_language="en", keyword_ledger=LEDGER)
    without = await audit_document("cv", VAULT, tailored_data=cv, provider=None, document_language="en")
    a, b = with_ledger.model_dump(mode="json"), without.model_dump(mode="json")
    a.pop("generated_at"), b.pop("generated_at")
    assert a == b


@pytest.mark.asyncio
async def test_a_sentence_naming_no_backed_row_opens_no_seam():
    judge = _Judge(answer=True)
    report = await audit_document(
        "cv", VAULT, tailored_data=_cv(bullets_0=["Coordinated vendor workshops across the region."]),
        provider=judge, document_language="en", keyword_ledger=LEDGER,
    )
    assert _by_loc(report)["work_history[0].bullets[0]"].verdict == "unverifiable"
    assert judge.batches == []


@pytest.mark.asyncio
async def test_employer_a_backing_owned_by_another_position_is_never_offered():
    """A fact (ids): the MES coupling is Kestrel's; rendered under Möwenhafen it is
    not offered to the judge, so a permissive judge cannot ground it there."""
    judge = _Judge(answer=True)
    report = await audit_document(
        "cv", VAULT, tailored_data=_cv(bullets_1=[MES_BULLET]), provider=judge,
        document_language="en", keyword_ledger=LEDGER,
    )
    v = _by_loc(report)["work_history[1].bullets[0]"]
    assert v.verdict != "grounded", v
    assert all(Q_MES not in b for b in judge.batches)


@pytest.mark.asyncio
async def test_summary_sentence_may_use_any_position_backing():
    judge = _Judge(answer=True)
    report = await audit_document(
        "cv", VAULT, tailored_data=_cv(summary="Laboratory IT lead experienced in LIMS integration with ERP systems."),
        provider=judge, document_language="en", keyword_ledger=LEDGER,
    )
    v = _by_loc(report)["summary[0]"]
    assert (v.verdict, v.checker) == ("grounded", "backing_judgement")


@pytest.mark.asyncio
async def test_letter_without_ledger_unchanged_with_ledger_judged():
    letter = {"body": {"paragraphs": [
        "At Kestrel Labortechnik GmbH, I connected the manufacturing execution system (MES) "
        "with the laboratory systems so batch data moves automatically.",
    ]}}
    judge = _Judge(answer=True)
    before = await audit_document("cover_letter", VAULT, letter_data=letter, provider=judge, document_language="en")
    after = await audit_document("cover_letter", VAULT, letter_data=letter, provider=judge,
                                 document_language="en", keyword_ledger=LEDGER)
    assert [c.verdict.verdict for c in before.claims] == ["unverifiable"]
    assert [(c.verdict.verdict, c.verdict.checker) for c in after.claims] == [("grounded", "backing_judgement")]


@pytest.mark.asyncio
async def test_a_credential_no_entry_names_is_unbacked_and_never_judged():
    """A same-language claim adding a credential no entry names: the seam's own
    facts check opens no candidate (the credential is not in the offered units)
    and E-2 then answers the fallback with `unbacked`. E-2's ``backing`` arm in
    ``_apply_credential_rule`` is shadowed by that check (an equivalent mutant —
    the offered units are a subset of the vault E-2 reads); kept as defence in
    depth for a future widening of the offered set."""
    en_vault = copy.deepcopy(VAULT)
    # E-2 never checks a pair whose vault may state evidence in another language
    # (``holds_other_language``) — so this vault is English throughout.
    en_vault["professional_summary"] = {"en": "Head of laboratory IT focused on system integration."}
    en_vault["work_experience"][0]["responsibilities"] = [
        "Connected the sample-receipt LIMS to the ERP system through a central interface platform.",
        "Coupled the manufacturing execution system to the laboratory systems for automatic batch data transfer.",
        "Built automated end-to-end tests for every system coupling before each release.",
    ]
    en_vault["work_experience"][0]["not_yet_reached"] = []
    en_vault["work_experience"][1]["responsibilities"] = ["Developed data pipelines between warehouse and accounting."]
    en_vault["skills"] = [{"name": "Interface architecture", "status": "confirmed"}]
    en_vault["metadata"] = {}
    ledger = copy.deepcopy(LEDGER)
    ledger[1]["backing"]["quote"] = en_vault["work_experience"][0]["responsibilities"][1]
    judge = _Judge(answer=True)
    claim = "As a certified Scrum Master I linked the MES and the lab systems so batch data flows on its own."
    report = await audit_document("cv", en_vault, tailored_data=_cv(bullets_0=[claim]), provider=judge,
                                  document_language="en", keyword_ledger=ledger)
    v = _by_loc(report)["work_history[0].bullets[0]"]
    assert (v.verdict, v.detail_key) == ("unbacked", "credentialUnbacked"), v
    assert judge.batches == []


@pytest.mark.asyncio
async def test_bare_verify_claim_without_sink_is_unchanged():
    from applire.services.oracle.audit import _claimable_index
    from applire.services.oracle.backing import ledger_backings

    idx = _claimable_index(VAULT)
    claim = Claim(text=MES_BULLET, location="work_history[0].bullets[0]", kind="bullet",
                  source_experience_id=_KESTREL)
    v = await verify_claim(claim, VAULT, _Judge(), index=idx, document_language="en",
                           ledger_backings=ledger_backings(LEDGER, VAULT, idx))
    assert (v.verdict, v.detail_key) == ("unverifiable", "noCloseEvidence")


# ── skill chips: the chip asks the ledger's own question (ruling WP1-1) ─────


@pytest.mark.asyncio
async def test_chip_naming_a_backed_row_is_grounded_on_its_quote_without_a_call():
    judge = _Judge(answer=False)
    report = await audit_document(
        "cv", VAULT, tailored_data=_cv(skills=["Integration testing"]), provider=judge,
        document_language="en", keyword_ledger=LEDGER,
    )
    v = _by_loc(report)["skills[0]"]
    assert (v.verdict, v.detail_key) == ("grounded", "ledgerBackingQuote"), v
    assert [e.ref for e in v.evidence if e.kind == "profile_path"] == ["work_experience[0].responsibilities[2]"]
    assert judge.batches == []


@pytest.mark.asyncio
async def test_chip_without_ledger_is_unchanged():
    report = await audit_document("cv", VAULT, tailored_data=_cv(skills=["Integration testing"]),
                                  provider=None, document_language="en")
    assert _by_loc(report)["skills[0]"].detail_key != "ledgerBackingQuote"


@pytest.mark.asyncio
async def test_chip_on_a_keyword_only_row_is_not_backed():
    ledger = copy.deepcopy(LEDGER)
    ledger[2]["sources"] = ["keyword"]
    report = await audit_document("cv", VAULT, tailored_data=_cv(skills=["Integration testing"]),
                                  provider=None, document_language="en", keyword_ledger=ledger)
    assert _by_loc(report)["skills[0]"].detail_key != "ledgerBackingQuote"


@pytest.mark.asyncio
async def test_chip_for_a_denied_concept_is_not_backed_even_if_a_row_says_direct():
    ledger = LEDGER + [_row("Kubernetes", ["Kubernetes"], Q_TEST, "work_experience[0].responsibilities[2]")]
    report = await audit_document("cv", VAULT, tailored_data=_cv(skills=["Kubernetes"]),
                                  provider=None, document_language="en", keyword_ledger=ledger)
    assert _by_loc(report)["skills[0]"].verdict != "grounded"


# ── seam: review_actions.skill_backed_now reads the same row (one reader) ────


def _recheck_backed(chip, ledger, vault=VAULT):
    import asyncio
    import uuid
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from applire.services import review_actions

    record = SimpleNamespace(job_analysis_id=uuid.uuid4())
    with (
        patch("applire.services.profile._get_latest",
              new=AsyncMock(return_value=SimpleNamespace(profile_json=vault))),
        patch("applire.services.gap._latest_gap_analysis",
              new=AsyncMock(return_value=SimpleNamespace(keyword_ledger=ledger))),
    ):
        return asyncio.run(review_actions.skill_backed_now(record, chip, None, user_id=uuid.uuid4()))


def test_removal_recheck_agrees_with_the_chip_verdict():
    assert _recheck_backed("Integration testing", LEDGER) is True
    assert _recheck_backed("Integration testing", None) is False
    edited = copy.deepcopy(VAULT)
    edited["work_experience"][0]["responsibilities"][2] = "Tests betreut."
    assert _recheck_backed("Integration testing", LEDGER, edited) is False
    ledger = LEDGER + [_row("Kubernetes", ["Kubernetes"], Q_TEST, "work_experience[0].responsibilities[2]")]
    assert _recheck_backed("Kubernetes", ledger) is False


# ── planted mismatches the judge must never be the only line on (review B/M) ─
#
# Each test hands the seam a PERMISSIVE judge (always "corresponds", citing the
# backing quote): what still stops the grant is deterministic.


async def _permissive(bullet, *, under=0, ledger=LEDGER, vault=VAULT):
    judge = _Judge(answer=True)
    cv = _cv(bullets_0=[bullet]) if under == 0 else _cv(bullets_1=[bullet])
    report = await audit_document("cv", vault, tailored_data=cv, provider=judge,
                                  document_language="en", keyword_ledger=ledger)
    return _by_loc(report)[f"work_history[{under}].bullets[0]"], judge


@pytest.mark.asyncio
@pytest.mark.parametrize("bullet", [
    # a spelled-out count and a year duration: the figure extractor sees neither
    "Connected the MES with the laboratory systems for eleven plants to transfer batch data automatically.",
    "Spent 9 years connecting the MES with the laboratory systems to transfer batch data automatically.",
], ids=["spelled-count", "year-duration"])
async def test_figure_the_offered_units_do_not_state_never_reaches_the_judge(bullet):
    v, judge = await _permissive(bullet)
    assert (v.verdict, v.detail_key) == ("unverifiable", "noCloseEvidence"), v
    assert judge.batches == []


@pytest.mark.asyncio
async def test_a_count_the_backing_states_still_reaches_the_judge():
    """Folding works across languages: "drei Standorte" backs "three sites"."""
    vault = copy.deepcopy(VAULT)
    vault["work_experience"][0]["responsibilities"][1] = Q_MES[:-1] + " an drei Standorten."
    ledger = copy.deepcopy(LEDGER)
    ledger[1]["backing"]["quote"] = vault["work_experience"][0]["responsibilities"][1]
    v, _ = await _permissive(
        "Connected the MES with the laboratory systems at three sites to transfer batch data automatically.",
        ledger=ledger, vault=vault,
    )
    assert (v.verdict, v.checker) == ("grounded", "backing_judgement")


@pytest.mark.asyncio
async def test_level_cross_language_credential_never_reaches_the_judge():
    """E-2 does not run on an other-language pair; the seam's own fact check does."""
    v, judge = await _permissive(
        "As a certified Scrum Master, connected the MES with the laboratory systems for batch data."
    )
    assert v.verdict != "grounded", v
    assert judge.batches == []


@pytest.mark.asyncio
async def test_employer_a_role_agnostic_unit_is_never_the_sole_proof():
    """The vault's bare skill "Schnittstellenarchitektur" has no owner; under
    Möwenhafen it may be context, never the cited proof of that employer."""
    ledger = LEDGER + [_row("Interface architecture", ["Interface architecture", "Schnittstellenarchitektur"],
                            "Schnittstellenarchitektur", "skills[0]")]
    judge = _Judge(answer=True, quote="Schnittstellenarchitektur")
    report = await audit_document(
        "cv", VAULT, tailored_data=_cv(bullets_1=["Owned the Schnittstellenarchitektur of the shipping systems."]),
        provider=judge, document_language="en", keyword_ledger=ledger,
    )
    v = _by_loc(report)["work_history[1].bullets[0]"]
    assert v.verdict != "grounded", v
    assert len(judge.batches) == 1  # offered as context, refused as proof


@pytest.mark.asyncio
async def test_a_sentence_touching_a_denial_is_never_offered():
    v, judge = await _permissive(
        "While I have not used Kubernetes, I connected the MES with the laboratory systems for batch data."
    )
    assert v.verdict != "grounded", v
    assert all("mode: backing" not in b for b in judge.batches)


@pytest.mark.asyncio
async def test_a_one_word_citation_never_grants_a_sentence():
    judge = _Judge(answer=True, quote="Laborsysteme")
    report = await audit_document(
        "cv", VAULT, tailored_data=_cv(bullets_0=[MES_BULLET]), provider=judge,
        document_language="en", keyword_ledger=LEDGER,
    )
    v = _by_loc(report)["work_history[0].bullets[0]"]
    assert (v.verdict, v.detail_key) == ("unverifiable", "noCloseEvidence")


@pytest.mark.asyncio
async def test_aspirational_cited_unit_never_grants_an_achieved_claim():
    vault = copy.deepcopy(VAULT)
    vault["work_experience"][0]["responsibilities"][1] = (
        "Das Fertigungsleitsystem soll an die Laborsysteme gekoppelt werden."
    )
    ledger = copy.deepcopy(LEDGER)
    ledger[1]["backing"]["quote"] = vault["work_experience"][0]["responsibilities"][1]
    v, _ = await _permissive(
        "Coupled the MES with the laboratory systems and delivered automatic batch data transfer.",
        ledger=ledger, vault=vault,
    )
    assert v.verdict != "grounded", v


@pytest.mark.asyncio
async def test_letter_form_judged_path_still_sees_a_backed_sentence():
    """Review M (ordering): a sentence the letter form half-covers (the judged
    form band) is offered to the form judge as before the seam existed; the
    backing candidate is withdrawn (sink/slot invariant: one candidate, one slot)."""
    letter = {"body": {"paragraphs": [
        "I connected the MES with the laboratory systems so batch data moves automatically.",
    ]}}
    form = {"motivation": "My work joined MES, laboratory systems and batch records."}
    judge = _Judge(answer=True)
    report = await audit_document("cover_letter", VAULT, letter_data=letter, provider=judge,
                                  document_language="en", keyword_ledger=LEDGER, form_inputs=form)
    assert [(c.verdict.verdict, c.verdict.checker) for c in report.claims] == [
        ("not_applicable", "letter_form_input")
    ]
    assert re.findall(r"\(mode: (\w+)\)", "\n".join(judge.batches)) == ["restatement"]


@pytest.mark.asyncio
async def test_letter_naming_no_employer_may_use_any_position_backing():
    letter = {"body": {"paragraphs": [
        "I connected the MES with the laboratory systems so batch data moves automatically.",
    ]}}
    report = await audit_document("cover_letter", VAULT, letter_data=letter, provider=_Judge(answer=True),
                                  document_language="en", keyword_ledger=LEDGER)
    assert [(c.verdict.verdict, c.verdict.checker) for c in report.claims] == [("grounded", "backing_judgement")]


@pytest.mark.asyncio
async def test_letter_naming_another_employer_gets_no_foreign_backing():
    letter = {"body": {"paragraphs": [
        "At Möwenhafen Spedition AG, I connected the MES with the laboratory systems so batch data moves.",
    ]}}
    judge = _Judge(answer=True)
    report = await audit_document("cover_letter", VAULT, letter_data=letter, provider=judge,
                                  document_language="en", keyword_ledger=LEDGER)
    assert report.claims[0].verdict.verdict != "grounded"
    assert all(Q_MES not in b for b in judge.batches)


@pytest.mark.asyncio
async def test_letter_truth_audit_hands_the_ledger_to_the_self_audit(monkeypatch):
    """Seam: ``cover_letter._letter_truth_audit`` → ``build_self_audit_report``."""
    from applire.services import cover_letter
    from applire.services.oracle import selfaudit

    seen: dict = {}

    async def _capture(profile_json, **kw):
        seen.update(kw)
        return {"claims": []}

    monkeypatch.setattr(selfaudit, "build_self_audit_report", _capture)
    monkeypatch.setattr(cover_letter, "get_provider", lambda: None)
    await cover_letter._letter_truth_audit(VAULT, {"body": {"paragraphs": ["x"]}}, form_inputs=None,
                                           document_language="en", keyword_ledger=LEDGER)
    assert seen["keyword_ledger"] is LEDGER


@pytest.mark.asyncio
async def test_summary_naming_another_employer_gets_no_foreign_backing():
    """Found by the planted-mismatch replay: a CV summary has no extractor anchor,
    so "… at Möwenhafen" was offered Kestrel's quote (the judge said no; the
    deterministic line now does)."""
    judge = _Judge(answer=True)
    report = await audit_document(
        "cv", VAULT, provider=judge, document_language="en", keyword_ledger=LEDGER,
        tailored_data=_cv(summary="Integration lead who ran MES integration at Möwenhafen Spedition AG."),
    )
    assert _by_loc(report)["summary[0]"].verdict != "grounded"
    assert all(Q_MES not in b for b in judge.batches)
