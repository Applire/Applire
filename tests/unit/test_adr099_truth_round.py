# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""T-F (ADR-099 cl. 4–6, UAT-2 fix round, WP-B): the truth round fixes an
unbacked claim before delivery, the delivered report — the SAME instrument the
round audited with — has no row of it, and the removal line lists it.

Mutations, each pinned by a named test:
  * the corrector returns the draft unchanged → the kind reappears as class-A
    residue on the delivered report (never vanishes);
  * agent-door render (``review_degradation is None``) and a degraded loop → a
    class-F kind with a silent residue renders as class U, never S;
  * the one instrument: the generation audit and the delivered audit are one
    function with the same provider and language arguments.
"""
from __future__ import annotations

import copy
import inspect
from unittest.mock import AsyncMock

import pytest

from applire.services import finding_registry as fr
from applire.services.attribution_round import (
    TRUTH_ROUND_LETTER_PER_FAMILY,
    TRUTH_ROUND_MAX_FINDINGS,
    build_truth_feedback,
    claim_present,
    run_truth_round,
    truth_findings,
)
from applire.services.oracle.selfaudit import build_self_audit_report

WID = "11111111-1111-1111-1111-111111111111"
VAULT = {
    "professional_summary": {"de": "Erfahrene Finanzbuchhalterin mit Schwerpunkt Monatsabschluss.", "en": ""},
    "work_experience": [{
        "id": WID, "company": "Muster Logistik GmbH", "title": "Finanzbuchhalterin",
        "start_date": "2016-01", "end_date": None, "location": "Bremen",
        "responsibilities": [
            "Monatsabschlüsse nach HGB vorbereitet",
            "Kreditorenbuchhaltung für 3 Gesellschaften geführt",
        ],
    }],
    "education": [], "skills": [{"name": "DATEV", "level": None, "category": "technical"}],
    "languages": [], "certifications": [],
    "contact": {"first_name": "Erika", "last_name": "Muster", "email": "erika@example.com"},
}
UNBACKED = "Monatsabschlüsse für 12 Gesellschaften nach HGB vorbereitet"
KEPT = "Kreditorenbuchhaltung für 3 Gesellschaften geführt"


def _prose(bullets):
    return {"summary": "Finanzbuchhalterin mit Schwerpunkt Monatsabschluss.",
            "work": [{"id": WID, "bullets": list(bullets)}], "skills": ["DATEV"]}


def _tailored(prose):
    return {
        "contact": {"name": "Erika Muster", "email": "erika@example.com"},
        "summary": prose["summary"],
        "work_history": [{"id": WID, "company": "Muster Logistik GmbH", "role": "Finanzbuchhalterin",
                          "start_date": "2016-01", "end_date": None, "bullets": prose["work"][0]["bullets"]}],
        "skills": prose["skills"],
    }


async def _audit(prose):
    """The one instrument, deterministic (no provider in a unit test)."""
    return await build_self_audit_report(VAULT, tailored_data=_tailored(prose), document_language="de")


class _Corrector:
    """A mock corrector: ``obey`` removes every bullet the feedback names."""

    def __init__(self, obey: bool):
        self.obey, self.calls, self.prompts = obey, 0, []

    async def aparse_json(self, prompt, **kw):
        self.calls += 1
        self.prompts.append((prompt, kw))
        draft = copy.deepcopy(self._draft)
        if self.obey:
            draft["work"][0]["bullets"] = [b for b in draft["work"][0]["bullets"] if f'"{b}"' not in prompt]
        return draft


async def _round(obey: bool):
    prose = _prose([UNBACKED, KEPT])
    report = await _audit(prose)
    corrector = _Corrector(obey)
    corrector._draft = prose
    res = await run_truth_round(
        prose, report=report, profile_json=VAULT, source_material="SOURCE",
        provider=corrector, system="SYS",
        prompt_fn=lambda d, fb, src: f"FEEDBACK:\n{fb}\nDRAFT:{d}",
    )
    return prose, report, res, corrector


def _flagged(report):
    return [c for c in report["claims"] if c["verdict"]["verdict"] in ("unbacked", "misattributed", "inflated")]


@pytest.mark.asyncio
async def test_round_1_fails_round_2_obeys_and_the_delivered_report_has_no_row():
    prose, report, res, corrector = await _round(obey=True)
    # round 1 = the writer's draft: the instrument flags the claim
    assert [c["claim"]["text"] for c in _flagged(report)] == [UNBACKED]
    assert [(f.family, f.kind) for f in res.findings] == [("unbacked", "oracle:figuresNoEvidence")]
    # round 2 = the corrector obeyed: one call, the claim is gone
    assert corrector.calls == 1 and res.applied
    assert not claim_present(UNBACKED, res.draft) and claim_present(KEPT, res.draft)
    delivered = await _audit(res.draft)            # the SAME function
    assert _flagged(delivered) == []
    out = fr.delivered_report(
        {"checks": [], "keywords": {}}, review_degradation=[], truthfulness=delivered,
        delivered_data=_tailored(res.draft), truth_round=res,
    )
    assert out["delivery"]["loop_clean"] is True
    assert [(r["text"], r["kind"], r["pinned"]) for r in out["delivery"]["removals"]] == [
        (UNBACKED, "oracle:figuresNoEvidence", False)
    ]
    assert out["delivery"]["fixed"] == ["oracle:figuresNoEvidence"]


@pytest.mark.asyncio
async def test_mutation_a_corrector_that_returns_the_draft_unchanged_leaves_class_a_residue():
    prose, report, res, corrector = await _round(obey=False)
    assert corrector.calls == 1 and not res.applied and res.removed == []
    delivered = await _audit(res.draft)
    rows = _flagged(delivered)
    assert [r["claim"]["text"] for r in rows] == [UNBACKED], "residue must never vanish"
    # the delivered row is stamped class A with its question (ADR-099 cl. 4(e)/6)
    assert (rows[0]["verdict"]["delivery_class"], rows[0]["verdict"]["resolver"]) == ("A", "ask_true")
    out = fr.delivered_report(
        {"checks": [], "keywords": {}}, review_degradation=[], truthfulness=delivered,
        delivered_data=_tailored(res.draft), truth_round=res,
    )
    assert out["delivery"]["removals"] == [] and out["delivery"]["fixed"] == []


@pytest.mark.asyncio
async def test_a_removal_the_delivered_document_carries_again_is_not_listed():
    prose, report, res, _ = await _round(obey=True)
    # a later round put the sentence back: it is residue, not a removal
    back = _tailored(_prose([UNBACKED, KEPT]))
    out = fr.delivered_report(
        {"checks": [], "keywords": {}}, review_degradation=[], truthfulness=await _audit(_prose([UNBACKED, KEPT])),
        delivered_data=back, truth_round=res,
    )
    assert out["delivery"]["removals"] == [] and out["delivery"]["fixed"] == []


@pytest.mark.asyncio
async def test_a_removal_that_hits_a_pinned_fact_is_marked_pinned():
    class Pin:
        quote, stale = "für 12 Gesellschaften", False

    _, _, res, _ = await _round(obey=True)
    rows = fr.removal_rows(res.removed, pins=[Pin()])
    assert rows[0]["pinned"] is True


def test_agent_door_and_degraded_loop_never_hide_a_class_f_kind():
    report = {"checks": [{"id": "duplicate-bullets", "status": "fail", "details_key": "duplicate-bullets"},
                         {"id": "narrative-evidence", "status": "fail", "details_key": "narrative-evidence-tag-only"}]}
    clean = fr.apply_delivery(report, review_degradation=[])
    assert [c["delivery_class"] for c in clean["checks"]] == ["S", "S"]
    for degradation in (None, [{"chain": "cv_tailoring", "stage": "review"}]):
        out = fr.apply_delivery(report, review_degradation=degradation)
        assert [(c["delivery_class"], c["resolver"]) for c in out["checks"]] == [("U", "regenerate")] * 2
        assert out["delivery"]["loop_clean"] is (None if degradation is None else False)


def test_t_s_a_silent_row_is_logged_for_the_operator(caplog):
    caplog.set_level("INFO", logger="applire.services.finding_registry")
    fr.apply_delivery({"checks": [{"id": "reading-order", "status": "fail"}]}, review_degradation=[])
    assert any("DELIVERY_SILENT" in r.message and "ats:reading-order" in r.message for r in caplog.records)


def test_a_reaudit_carries_the_generation_removals():
    prev = {"delivery": {"loop_clean": True, "removals": [{"text": "x", "kind": "oracle@unbacked"}], "fixed": ["k"]}}
    out = fr.apply_delivery({"checks": []}, review_degradation=[], previous=prev)
    assert out["delivery"]["removals"] == prev["delivery"]["removals"] and out["delivery"]["fixed"] == ["k"]


def test_the_round_is_bounded_and_truthfulness_comes_first():
    claims = [{"claim": {"text": f"u{i}", "location": f"work_history[0].bullets[{i}]"},
               "verdict": {"verdict": "unbacked", "detail_key": "figuresNoEvidence"}} for i in range(12)]
    claims.append({"claim": {"text": "m", "location": "work_history[0].bullets[20]"},
                   "verdict": {"verdict": "misattributed", "detail_key": "misattributed"}})
    claims.append({"claim": {"text": "i", "location": "summary"},
                   "verdict": {"verdict": "inflated", "detail_key": "inflatedAspirational"}})
    got = truth_findings({"claims": claims})
    assert len(got) == TRUTH_ROUND_MAX_FINDINGS and got[0].family == "misattributed"
    assert all(f.family != "inflated" for f in got), "inflated is a question, never a writer instruction (ADR-062)"
    letter = truth_findings({"claims": claims}, document="cover_letter")
    assert [f.family for f in letter].count("unbacked") == TRUTH_ROUND_LETTER_PER_FAMILY


def test_the_feedback_quotes_the_document_never_the_vault():
    f = truth_findings({"claims": [{"claim": {"text": UNBACKED, "location": "work_history[0].bullets[0]"},
                                    "verdict": {"verdict": "unbacked", "detail_key": "figuresNoEvidence",
                                                "evidence": [{"kind": "profile_path", "ref": "work_experience[0]"}]}}]})
    fb = build_truth_feedback(f, VAULT)
    assert f'"{UNBACKED}"' in fb and "remove the unbacked claim, or narrow it" in fb
    assert "Kreditorenbuchhaltung" not in fb


@pytest.mark.asyncio
async def test_a_failing_corrector_call_is_one_call_and_ships_the_draft():
    prose = _prose([UNBACKED])
    provider = AsyncMock()
    provider.aparse_json.side_effect = RuntimeError("down")
    res = await run_truth_round(prose, report=await _audit(prose), profile_json=VAULT,
                                source_material="S", provider=provider, system="SYS")
    assert res.draft is prose and res.llm_calls == 1 and not res.applied


@pytest.mark.asyncio
async def test_a_letter_round_that_drops_the_body_is_rejected():
    letter = {"body": {"paragraphs": ["Sehr geehrte Damen und Herren,", "A.", "B."]}}

    class P:
        async def aparse_json(self, prompt, **kw):
            return {"body": {"paragraphs": ["only"]}}

    rep = {"claims": [{"claim": {"text": "A.", "location": "body[1]"},
                       "verdict": {"verdict": "unbacked", "detail_key": None}}]}
    res = await run_truth_round(letter, report=rep, profile_json=VAULT, source_material="S",
                                provider=P(), system="SYS", document="cover_letter",
                                prompt_fn=lambda d, f, s: f)
    assert res.draft is letter and not res.applied


def test_one_instrument_cv_and_letter():
    """ADR-099 cl. 4(b): the loop audit and the delivered audit are one function
    with provider + document language (cv.py used to pass neither)."""
    import applire.services.cover_letter as cl
    import applire.services.cv as cv

    src = inspect.getsource(cv)
    assert "attribution_report = await _truth_audit(" in src
    assert "record.truthfulness_report = await _truth_audit(" in src
    assert src.count("build_self_audit_report(") == 1   # only inside _truth_audit
    tsrc = inspect.getsource(cv._truth_audit)
    assert "provider=get_provider()" in tsrc and "document_language=document_language" in tsrc
    lsrc = inspect.getsource(cl)
    assert "report=await _letter_truth_audit(" in lsrc
    assert "cl.truthfulness_report = await _letter_truth_audit(" in lsrc
    assert lsrc.count("build_self_audit_report(") == 1


def test_a_duplicate_project_goes_the_redundancy_route(monkeypatch):
    """ADR-099 cl. 8: the ATS duplicate-project pair reaches the corrector as one
    sentence (no deterministic drop); none when the document has no pair."""
    import applire.services.bullet_redundancy_signal as brs

    monkeypatch.setattr(brs, "redundant_bullet_pairs", lambda d: [])
    monkeypatch.setattr(brs, "duplicate_project_pairs", lambda d: [("A > Projekte", "Atlas", "Projekte", "Atlas (2021)")])
    issues = brs.redundancy_signal_issues({"x": 1})
    assert len(issues) == 1 and '"Atlas"' in issues[0].text and "appears in only one copy, so nothing is lost" in issues[0].text
    monkeypatch.setattr(brs, "duplicate_project_pairs", lambda d: [])
    assert brs.redundancy_signal_issues({"x": 1}) == []


@pytest.mark.asyncio
async def test_t_bound_the_second_language_round_stays_within_its_call_bound():
    """T-bound (ADR-099): a provider that fails every round — the reviewer never
    approves, the refiner never translates — spends at most the loop's declared
    calls, and the draft ships unchanged-in-shape."""
    from applire.prompts.review_cv_language import CV_LANGUAGE_REVIEW_SYSTEM_PROMPT
    from applire.services import cv as cv_mod

    foreign = "Managed the monthly close for twelve entities"
    prose = _prose([foreign])

    class Stubborn:
        calls = 0

        async def aparse_json(self, prompt, **kw):
            Stubborn.calls += 1
            if kw.get("system") == CV_LANGUAGE_REVIEW_SYSTEM_PROMPT:
                return {"approved": False, "feedback": "still English",
                        "issues": [{"text": "still English", "severity": "blocking"}]}
            return copy.deepcopy(prose)

        async def acomplete(self, prompt, **kw):
            return await self.aparse_json(prompt, **kw)

    out = await cv_mod._second_language_round(
        prose, compose=_tailored, document_language="de", provider=Stubborn(),
        keyword_ledger=None, budget=None, v2_shape_check=False, cv_id="t",
    )
    bound = 2 * cv_mod.CV_LANGUAGE_REVIEW_MAX_RETRIES + 1
    assert 0 < Stubborn.calls <= bound, (Stubborn.calls, bound)
    assert out["work"][0]["bullets"] == [foreign]

    class Down:
        calls = 0

        async def aparse_json(self, prompt, **kw):
            Down.calls += 1
            raise RuntimeError("down")

        acomplete = aparse_json

    out = await cv_mod._second_language_round(
        prose, compose=_tailored, document_language="de", provider=Down(),
        keyword_ledger=None, budget=None, v2_shape_check=False, cv_id="t",
    )
    assert out["work"][0]["bullets"] == [foreign] and Down.calls <= bound


@pytest.mark.asyncio
async def test_a_narrowed_claim_still_flagged_is_residue_not_a_removal():
    """cl. 10 one condition, one place: the corrector narrowed an unbacked claim
    and the remainder is STILL unbacked → it renders as its class-A card, never
    also on the removal line; a remainder the Oracle backs is named narrowed_to."""
    from applire.services.attribution_round import TruthFinding, TruthRoundResult

    claim = "Treasury-Management für 15 Bankkonten verantwortet"
    f = TruthFinding(family="unbacked", kind="oracle:figuresNoEvidence", text=claim,
                     location="work_history[0].bullets[1]", detail_key="figuresNoEvidence")
    tr = TruthRoundResult(draft={}, findings=[f], removed=[f], applied=True, llm_calls=1)
    still = {"claims": [{"claim": {"text": "Treasury-Management verantwortet"},
                         "verdict": {"verdict": "unbacked", "detail_key": "skillNoEvidence"}}]}
    out = fr.delivered_report({"checks": []}, review_degradation=[], truthfulness=still,
                              delivered_data={"x": "Treasury-Management verantwortet"}, truth_round=tr)
    assert out["delivery"]["removals"] == [] and out["delivery"]["fixed"] == []
    backed = {"claims": [{"claim": {"text": "Treasury-Management verantwortet"},
                          "verdict": {"verdict": "grounded"}}]}
    out = fr.delivered_report({"checks": []}, review_degradation=[], truthfulness=backed,
                              delivered_data={"x": "Treasury-Management verantwortet"}, truth_round=tr)
    assert [r.get("narrowed_to") for r in out["delivery"]["removals"]] == ["Treasury-Management verantwortet"]
    assert out["delivery"]["fixed"] == ["oracle:figuresNoEvidence"]
