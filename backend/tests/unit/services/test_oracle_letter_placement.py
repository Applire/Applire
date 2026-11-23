# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet fix round after adv/holds (D-6 / D-7) — where a reader places a letter
sentence that names no employer is the placement seam's judgement.

Recorded turn (delivery-q10, 2026-11-18, Daniel's delivered letter): the Finleap
paragraph opened "At Finleap Build GmbH, I integrated Stripe Checkout …" and went on
"I also built Prometheus and Grafana monitoring …" — every one of those facts is
Cargonaut's; both hiring managers read the work as Finleap's. WP-L first answered
with a lexical reader (subject pronoun, clause negation); the adversarial pass on the
holds broke it both ways: it missed "not only … but also", passives, possessives and
";" joins (D-6) and accused true sentences in "my current role" (D-7). The reader is
gone; the deterministic layer computes which positions WOULD raise the flag, the
judgement says where the paragraph places the claim, and the code compares.

Synthetic names stand in for the recorded persona.
"""
from __future__ import annotations

import re

import pytest

from applire.prompts.oracle_judgement import ORACLE_JUDGEMENT_SYSTEM_PROMPT, build_judgement_user_prompt
from applire.prompts.oracle_triage import ORACLE_TRIAGE_SYSTEM_PROMPT
from applire.services.oracle import audit_document
from applire.services.oracle.audit import _placement_cited_unit, _position_lines
from applire.services.oracle.extract import extract_claims_from_letter
from applire.services.oracle.matchers.vault import EvidenceUnit

PROFILE = {
    "personal_info": {"name": "Dario Beispiel"},
    "work_experience": [
        {
            "id": "w-nordwind",
            "company": "Nordwind Logistik GmbH",
            "role": "Senior Backend Engineer",
            "start_date": "2021-03",
            "is_current": True,
            "responsibilities": [
                "Own the GitLab CI/CD pipelines for four services.",
                "Introduced contract tests between the tracking API and three consumer teams.",
                "Built the Prometheus and Grafana setup, including RED-metrics dashboards for all four services.",
                "Defined SLOs with error budgets for the tracking API.",
                "Mentor two mid-level engineers.",
                "Run the team's weekly design-review session.",
            ],
        },
        {
            "id": "w-brueckenbau",
            "company": "Brückenbau Fintech GmbH",
            "role": "Backend Engineer",
            "start_date": "2017-06",
            "end_date": "2021-02",
            "responsibilities": [
                "Integrated Stripe checkout with webhooks and reconciled payouts against invoices.",
                "Wrote the team's incident runbook.",
            ],
        },
    ],
}

NORDWIND_LINE = "Senior Backend Engineer, Nordwind Logistik GmbH — current position, since 2021-03"
BRUECKENBAU_LINE = "Backend Engineer, Brückenbau Fintech GmbH — previous position, 2017-06 to 2021-02"

RUN = (
    "At Brückenbau Fintech GmbH, I integrated Stripe checkout with webhooks and "
    "reconciled payouts against invoices. "
)
FOREIGN = (
    "I also built Prometheus and Grafana monitoring with RED-metrics dashboards "
    "and owned GitLab CI/CD pipelines."
)
OWN = "I also wrote the team's incident runbook."


def _letter(*sentences: str) -> dict:
    return {"body": {"paragraphs": ["Dear Sir or Madam,", RUN + " ".join(sentences)]}}


def _claim(report, needle):
    hits = [r for r in report.claims if needle in r.claim.text]
    assert hits, f"no claim containing {needle!r}: {[r.claim.text for r in report.claims]}"
    return hits[0]


_ITEM_RE = re.compile(r"^ITEM (\d+) \(mode: (\w+)\):\nCLAIM: (.*)$", re.M)


class _Judge:
    """Triage: every sentence is a candidate claim. Placement: ``answer(claim)``
    returns ``(corresponds, vault_quote)``. Every other judgement mode: uncertain."""

    def __init__(self, answer):
        self.answer = answer
        self.placement_prompts: list[str] = []

    async def aparse_json(self, prompt, system=None, **kw):
        if system == ORACLE_TRIAGE_SYSTEM_PROMPT:
            sentences = re.findall(r"^SENTENCE \d+: (.*)$", prompt, flags=re.M)
            return {"items": [{"index": i, "classification": "candidate-claim", "sentence_quote": s}
                              for i, s in enumerate(sentences)]}
        assert system == ORACLE_JUDGEMENT_SYSTEM_PROMPT
        items = []
        for idx, mode, claim in _ITEM_RE.findall(prompt):
            if mode == "placement":
                self.placement_prompts.append(prompt)
                corresponds, quote = self.answer(claim)
            else:
                corresponds, quote = "uncertain", ""
            items.append({"index": int(idx), "corresponds": corresponds, "vault_quote": quote})
        return {"items": items}


def _placed(line):
    return lambda claim: (True, line)


# ── the facts the code computes ──────────────────────────────────────────────


def test_position_lines_state_current_previous_and_earlier():
    profile = {"work_experience": [
        {"id": "a", "company": "Alpha AG", "role": "Lead", "start_date": "2022-01", "is_current": True},
        {"id": "c", "company": "Gamma GmbH", "role": "Planner", "start_date": "2014-09", "end_date": "2018-03"},
        {"id": "b", "company": "Beta GmbH", "role": "Supervisor", "start_date": "2018-04", "end_date": "2021-12"},
    ]}
    assert _position_lines(profile) == [
        ("a", "Lead, Alpha AG — current position, since 2022-01"),
        ("b", "Supervisor, Beta GmbH — previous position, 2018-04 to 2021-12"),
        ("c", "Planner, Gamma GmbH — earlier position, 2014-09 to 2018-03"),
    ]


def test_the_prompt_shows_the_paragraph_and_the_positions():
    prompt = build_judgement_user_prompt([("I also built X.", [NORDWIND_LINE, BRUECKENBAU_LINE], "placement", None,
                                           RUN + "I also built X.")])
    assert "ITEM 0 (mode: placement):\nCLAIM: I also built X.\nLETTER PARAGRAPH: At Brückenbau" in prompt
    assert f"  [1] {BRUECKENBAU_LINE}" in prompt
    assert "- placement:" in ORACLE_JUDGEMENT_SYSTEM_PROMPT


def test_a_quote_must_name_one_position():
    units = [
        EvidenceUnit(path="position:n", text=NORDWIND_LINE, text_norm="", owner_ids=frozenset({"n"})),
        EvidenceUnit(path="position:b", text=BRUECKENBAU_LINE, text_norm="", owner_ids=frozenset({"b"})),
    ]
    assert _placement_cited_unit("Backend Engineer", units) is None  # in both lines
    assert _placement_cited_unit("Backend Engineer, Brückenbau Fintech GmbH", units).path == "position:b"
    assert _placement_cited_unit("Nordwind Logistik GmbH", units).path == "position:n"
    assert _placement_cited_unit("Acme", units) is None
    assert _placement_cited_unit(None, units) is None
    two_roles = [
        EvidenceUnit(path="position:m", text="Schichtleiter, Talwerk GmbH — previous position", text_norm="",
                     owner_ids=frozenset({"m"})),
        EvidenceUnit(path="position:x", text="Mechaniker, Talwerk GmbH — earlier position", text_norm="",
                     owner_ids=frozenset({"x"})),
    ]
    assert _placement_cited_unit("Talwerk GmbH", two_roles) is None
    assert _placement_cited_unit("Mechaniker, Talwerk GmbH", two_roles).path == "position:x"


def test_extraction_reads_no_subject_any_more():
    claims = extract_claims_from_letter(_letter(FOREIGN), PROFILE)
    stmt = next(c for c in claims if "Prometheus" in c.text)
    assert stmt.anchor_inherited is True and stmt.source_experience_id == "w-brueckenbau"
    assert not hasattr(stmt, "run_statement")


# ── the judgement decides, the code compares ────────────────────────────────


@pytest.mark.asyncio
async def test_placed_at_the_paragraphs_employer_with_foreign_evidence_is_misattributed(monkeypatch):
    from datetime import date

    from applire.services.oracle import audit as audit_mod

    monkeypatch.setattr(audit_mod, "_placement_today", lambda: date(2026, 11, 23))
    judge = _Judge(_placed(BRUECKENBAU_LINE))
    report = await audit_document("cover_letter", PROFILE, letter_data=_letter(FOREIGN), provider=judge)
    result = _claim(report, "I also built Prometheus")
    assert result.verdict.verdict == "misattributed", result.verdict
    assert result.verdict.checker == "attribution"
    assert "places it under Backend Engineer, Brückenbau Fintech GmbH" in result.verdict.detail
    assert any("work_experience[0]" in e.ref for e in result.verdict.evidence), result.verdict.evidence
    assert report.judgement_unavailable == 0
    # The judge saw the paragraph and both positions.
    (prompt,) = judge.placement_prompts
    # …and the month the letter is read in, which is "now" for "seit zwei Jahren".
    assert f"LETTER PARAGRAPH: (written 2026-11) {RUN}{FOREIGN}" in prompt and NORDWIND_LINE in prompt


@pytest.mark.asyncio
async def test_a_quote_with_the_evidence_index_still_counts():
    judge = _Judge(lambda claim: (True, f"[1] {BRUECKENBAU_LINE}"))
    report = await audit_document("cover_letter", PROFILE, letter_data=_letter(FOREIGN), provider=judge)
    assert _claim(report, "I also built Prometheus").verdict.verdict == "misattributed"


@pytest.mark.asyncio
async def test_placed_in_the_current_role_is_not_accused():
    """D-7: "Today, in my current role, I own …" inside the earlier employer's paragraph."""
    sentence = "Today, in my current role, I own the GitLab CI/CD pipelines for four services."
    report = await audit_document("cover_letter", PROFILE, letter_data=_letter(sentence),
                                  provider=_Judge(_placed(NORDWIND_LINE)))
    result = _claim(report, "GitLab CI/CD pipelines")
    assert result.verdict.verdict != "misattributed", result.verdict
    assert report.judgement_unavailable == 0


@pytest.mark.parametrize("answer", [(False, ""), ("uncertain", "")])
@pytest.mark.asyncio
async def test_no_position_or_cannot_tell_never_accuses(answer):
    report = await audit_document("cover_letter", PROFILE, letter_data=_letter(FOREIGN),
                                  provider=_Judge(lambda claim: answer))
    assert _claim(report, "I also built Prometheus").verdict.verdict != "misattributed"
    assert report.judgement_unavailable == 0


@pytest.mark.parametrize("answer", [(True, "Backend Engineer"), (True, "Acme GmbH"), ("yes", BRUECKENBAU_LINE)])
@pytest.mark.asyncio
async def test_an_unusable_answer_never_accuses_and_is_counted(answer):
    report = await audit_document("cover_letter", PROFILE, letter_data=_letter(FOREIGN),
                                  provider=_Judge(lambda claim: answer))
    assert _claim(report, "I also built Prometheus").verdict.verdict != "misattributed"
    assert report.judgement_unavailable == 1


@pytest.mark.asyncio
async def test_without_a_provider_nothing_is_accused_and_the_gap_is_counted():
    report = await audit_document("cover_letter", PROFILE, letter_data=_letter(FOREIGN))
    assert _claim(report, "I also built Prometheus").verdict.verdict != "misattributed"
    # Every sentence's triage is unavailable too (ADR-068, one per sentence here);
    # the placement adds one.
    assert report.judgement_unavailable == len(report.claims) + 1


@pytest.mark.asyncio
async def test_the_paragraphs_own_work_placed_there_still_grounds():
    report = await audit_document("cover_letter", PROFILE, letter_data=_letter(OWN),
                                  provider=_Judge(_placed(BRUECKENBAU_LINE)))
    assert _claim(report, "incident runbook").verdict.verdict == "grounded"


@pytest.mark.asyncio
async def test_own_work_placed_elsewhere_is_misattributed():
    """The comparison runs both ways: Brückenbau's runbook read as Nordwind's work."""
    letter = {"body": {"paragraphs": ["Dear Sir or Madam,",
                                      "In my current role I own the GitLab CI/CD pipelines for four services. "
                                      "I also wrote the team's incident runbook."]}}
    report = await audit_document("cover_letter", PROFILE, letter_data=letter,
                                  provider=_Judge(_placed(NORDWIND_LINE)))
    assert _claim(report, "incident runbook").verdict.verdict == "misattributed"


@pytest.mark.asyncio
async def test_a_sentence_naming_its_employer_is_not_asked():
    """Structurally certain: the sentence names the position; the named-anchor path decides."""
    sentence = "At Nordwind I later built the Prometheus and Grafana setup, including RED-metrics dashboards."
    judge = _Judge(_placed(BRUECKENBAU_LINE))
    letter = {"body": {"paragraphs": ["Dear Sir or Madam,", sentence]}}
    report = await audit_document("cover_letter", PROFILE, letter_data=letter, provider=judge)
    assert judge.placement_prompts == []
    assert _claim(report, "Prometheus").verdict.verdict != "misattributed"


@pytest.mark.asyncio
async def test_a_sentence_with_no_anchor_at_all_is_asked():
    """D-6: "In my previous role I built …" in a paragraph that names no employer."""
    letter = {"body": {"paragraphs": ["Dear Sir or Madam,",
                                      "In my previous role I built Prometheus and Grafana monitoring with "
                                      "RED-metrics dashboards and owned GitLab CI/CD pipelines."]}}
    report = await audit_document("cover_letter", PROFILE, letter_data=letter,
                                  provider=_Judge(_placed(BRUECKENBAU_LINE)))
    assert _claim(report, "Prometheus").verdict.verdict == "misattributed"


@pytest.mark.asyncio
async def test_a_sentence_the_triage_exempted_is_not_asked():
    """A motivation sentence triage already set aside (not_applicable) stays aside."""
    sentence = "I would like to bring my Prometheus and Grafana monitoring experience to your team."

    class _Exempting(_Judge):
        async def aparse_json(self, prompt, system=None, **kw):
            if system == ORACLE_TRIAGE_SYSTEM_PROMPT:
                sentences = re.findall(r"^SENTENCE \d+: (.*)$", prompt, flags=re.M)
                return {"items": [{"index": i, "sentence_quote": s,
                                   "classification": "epistolary-form" if s == sentence else "candidate-claim"}
                                  for i, s in enumerate(sentences)]}
            return await super().aparse_json(prompt, system=system, **kw)

    judge = _Exempting(_placed(BRUECKENBAU_LINE))
    report = await audit_document("cover_letter", PROFILE, letter_data=_letter(sentence), provider=judge)
    assert _claim(report, "would like to bring").verdict.verdict == "not_applicable"
    assert all(sentence not in p for p in judge.placement_prompts)


@pytest.mark.asyncio
async def test_a_sentence_naming_an_employer_ambiguously_is_not_asked():
    """Two roles at one employer: the sentence still names its employer (the carry
    rule decides), so a reader needs no judgement about where it is placed."""
    letter = {"body": {"paragraphs": [
        "At Talwerk Umformtechnik GmbH, I created real-time OEE transparency and raised OEE from 61% to 73% in 18 months."
    ]}}
    judge = _Judge(_placed("Schichtleiter, Talwerk Umformtechnik GmbH — previous position, 2011-08 to 2017-03"))
    await audit_document("cover_letter", TWO_ROLE_PROFILE, letter_data=letter, provider=judge)
    assert judge.placement_prompts == []


@pytest.mark.asyncio
async def test_a_claim_no_placement_could_matter_for_is_not_asked():
    """Evidence no position owns exclusively (a skill) raises no flag anywhere."""
    profile = {**PROFILE, "skills": [{"name": "Kubernetes"}]}
    judge = _Judge(_placed(BRUECKENBAU_LINE))
    report = await audit_document("cover_letter", profile, letter_data=_letter("I also run Kubernetes."),
                                  provider=judge)
    assert judge.placement_prompts == []
    assert _claim(report, "Kubernetes").verdict.verdict != "misattributed"


@pytest.mark.asyncio
async def test_a_one_position_vault_is_never_asked():
    """No other position owns anything, so no placement could raise a flag."""
    profile = {**PROFILE, "work_experience": PROFILE["work_experience"][:1]}
    judge = _Judge(_placed(NORDWIND_LINE))
    await audit_document("cover_letter", profile, letter_data=_letter(FOREIGN), provider=judge)
    assert judge.placement_prompts == []


@pytest.mark.asyncio
async def test_a_multi_fact_statement_backed_only_by_another_employers_union_is_misattributed():
    """delivery-1b / delivery-2b Daniel: "I also mentor two mid-level engineers and
    run weekly design reviews." after the earlier employer's sentence — no single
    bullet backs it; the other employer's union does (the named-anchor re-grade)."""
    sentence = "I also mentor two mid-level engineers and run the team's weekly design reviews."
    report = await audit_document("cover_letter", PROFILE, letter_data=_letter(sentence),
                                  provider=_Judge(_placed(BRUECKENBAU_LINE)))
    result = _claim(report, "mentor two mid-level engineers")
    assert result.verdict.verdict == "misattributed", result.verdict
    assert "union" in (result.verdict.detail or "")


# ── the carry after an ambiguous employer sentence (WP-L delivery run, case 5) ──

TWO_ROLE_PROFILE = {
    "personal_info": {"name": "Sven Beispiel"},
    "work_experience": [
        {"id": "w-kunststoff", "company": "Lindenau Kunststoff GmbH", "role": "Produktionsleiter",
         "start_date": "2017-04", "is_current": True,
         "responsibilities": ["Created real-time OEE transparency and raised OEE from 61% to 73% in 18 months."]},
        {"id": "w-blech-meister", "company": "Talwerk Umformtechnik GmbH", "role": "Schichtleiter",
         "start_date": "2011-08", "end_date": "2017-03",
         "responsibilities": ["Led a shift of 14 employees in sheet-metal forming."]},
        {"id": "w-blech-mech", "company": "Talwerk Umformtechnik GmbH", "role": "Industriemechaniker",
         "start_date": "2004-08", "end_date": "2011-07",
         "responsibilities": ["Maintained presses."]},
    ],
}


def _two_role_letter():
    return {"body": {"paragraphs": [
        "At Lindenau Kunststoff GmbH, I hold budget responsibility for personnel. "
        "At Talwerk Umformtechnik GmbH, I led a shift of 14 employees. "
        "I also created real-time OEE transparency and raised OEE from 61% to 73% in 18 months."
    ]}}


def test_an_ambiguous_employer_sentence_moves_the_carry_to_that_employer():
    claims = extract_claims_from_letter(_two_role_letter(), TWO_ROLE_PROFILE)
    oee = next(c for c in claims if "OEE transparency" in c.text)
    assert oee.anchor_inherited is True
    assert oee.source_experience_id in {"w-blech-meister", "w-blech-mech"}, oee


@pytest.mark.asyncio
async def test_the_earlier_employers_work_placed_after_it_is_misattributed():
    line = "Schichtleiter, Talwerk Umformtechnik GmbH — previous position, 2011-08 to 2017-03"
    report = await audit_document("cover_letter", TWO_ROLE_PROFILE, letter_data=_two_role_letter(),
                                  provider=_Judge(_placed(line)))
    assert _claim(report, "OEE transparency").verdict.verdict == "misattributed"


def test_a_sentence_naming_two_employers_ends_the_carry():
    letter = {"body": {"paragraphs": [
        "At Lindenau Kunststoff GmbH, I hold budget responsibility for personnel. "
        "Both Talwerk Umformtechnik and Lindenau Kunststoff taught me lean methods. "
        "I also created real-time OEE transparency."
    ]}}
    claims = extract_claims_from_letter(letter, TWO_ROLE_PROFILE)
    oee = next(c for c in claims if "OEE transparency" in c.text)
    assert oee.source_experience_id is None, oee
