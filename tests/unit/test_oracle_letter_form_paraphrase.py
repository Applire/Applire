# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""B3-10 follow-up (Hazelnut build 3 re-probe on 00190b6b, 2026-10-23).

On a real luna letter the writer PARAPHRASES the form input — "3 Monate" became
"drei Monate", the motivation was reworded, and the sentence triage called the
120-person sentence "no claim". The near-verbatim rule never marked a sentence:
the "Aus deiner Eingabe im Formular" section stayed empty. The shapes below are
the persisted ones (``reprobe/marcus/db_letter.json``, the Katrin letter of the
re-probe's LLM log).

The mark now fires on the paraphrase — spelled-out counts folded, a short
answer's own question words as frame, a triage-exempt sentence included, and
below the floor the restatement judge asked against the form text (ADR-062) —
and never on a sentence whose fact the form does not state.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from applire.prompts.oracle_judgement import ORACLE_JUDGEMENT_SYSTEM_PROMPT
from applire.prompts.oracle_triage import ORACLE_TRIAGE_SYSTEM_PROMPT
from applire.services.oracle.audit import audit_document
from .test_oracle_letter_form_input import FORM, VAULT, _at, _letter, db  # noqa: F401

NOTICE_DE = "Meine Kündigungsfrist beträgt drei Monate zum Quartalsende."
#: reprobe/marcus body.paragraphs[1][3] — triage called it no claim.
TRIAGED_120 = "Die dauerhafte Führung von rund 120 Mitarbeitenden wäre für mich der nächste Schritt."
FORM_KATRIN = {
    "tone": "formal", "salary": None, "recipient_name": None, "recipient_company": "Alpenstahl Holding AG",
    "motivation": ("Ich möchte künftig ein Team von sechs Personen führen und im Konzernabschluss mehr "
                   "Verantwortung übernehmen, weil mich die Verbindung von Berichtswesen und Planung genau an "
                   "dieser Stelle reizt."),
    "availability": "3 Monate zum Quartalsende",
}
#: Katrin's delivered letter, body.paragraphs[1][1] — below the coverage floor.
KATRIN_REWORDED = ("Die Verbindung von Berichtswesen und Planung in Ihrem industriellen Konzernumfeld spricht "
                   "mich besonders an.")


class _Stub:
    """Triage per sentence (default: a candidate claim, i.e. graded); the judge
    answers every item ``corresponds`` = *judge* and records what it was asked."""

    def __init__(self, judge=True, triage=None, quote=None):
        self.judge, self.triage, self.quote = judge, triage or {}, quote
        self.judge_prompts: list[str] = []

    async def aparse_json(self, prompt, system=None, **kw):
        if system == ORACLE_TRIAGE_SYSTEM_PROMPT:
            sentences = re.findall(r"^SENTENCE \d+: (.*)$", prompt, flags=re.M)
            return {"items": [{"index": i, "classification": self.triage.get(s, "candidate-claim"),
                               "sentence_quote": s} for i, s in enumerate(sentences)]}
        if system == ORACLE_JUDGEMENT_SYSTEM_PROMPT:
            self.judge_prompts.append(prompt)
            items = re.findall(r"ITEM (\d+) \(mode: [\w-]+\):\nCLAIM: .*\nVAULT EVIDENCE:\n  \[0\] (.*)", prompt)
            return {"items": [{"index": int(i), "corresponds": self.judge, "vault_quote": self.quote or ev[:40]}
                              for i, ev in items]}
        raise RuntimeError("unexpected call")


async def _audit(sentence, form=FORM, provider=None, vault=VAULT):
    report = await audit_document("cover_letter", vault, letter_data=_letter(sentence), provider=provider,
                                  document_language="de", form_inputs=form)
    return _at(report, 0)


# ── the paraphrase shapes of the re-probe ─────────────────────────────────────


@pytest.mark.asyncio
async def test_a_spelled_out_notice_period_is_marked():
    """reprobe/marcus [4][1] and both Katrin letters: "drei" for the typed "3"."""
    v = await _audit(NOTICE_DE)
    assert (v.checker, v.detail_params) == ("letter_form_input", {"field": "availability"})
    assert [(e.kind, e.ref, e.excerpt) for e in v.evidence] == [
        ("letter_form", "pre_gen_inputs.availability", "3 Monate zum Quartalsende")]


@pytest.mark.asyncio
async def test_an_english_notice_period_reads_the_fields_own_words():
    form = dict(FORM, availability="3 months")
    v = await _audit("My notice period is three months.", form=form)
    assert (v.checker, v.detail_params) == ("letter_form_input", {"field": "availability"})


@pytest.mark.asyncio
async def test_a_sentence_triage_exempts_still_gets_the_mark():
    """reprobe/marcus [1][3]: triage said "no claim", so the rule never saw it."""
    stub = _Stub(triage={TRIAGED_120: "employer-fact"})
    v = await _audit(TRIAGED_120, provider=stub)
    assert (v.verdict, v.checker, v.detail_params) == ("not_applicable", "letter_form_input", {"field": "motivation"})
    assert stub.judge_prompts == []


@pytest.mark.asyncio
async def test_a_triage_exempt_sentence_the_form_never_stated_keeps_its_triage():
    other = "Ihre Verpackungen für Konsumgüter verbinden Produktionsfokus mit Nachhaltigkeit."
    v = await _audit(other, provider=_Stub(triage={other: "employer-fact"}))
    assert (v.verdict, v.checker) == ("not_applicable", "sentence_triage")


# ── laundering negatives: a fact the form does not state is never marked ──────


@pytest.mark.parametrize("sentence, why", [
    ("Meine Kündigungsfrist beträgt sechs Monate zum Quartalsende.", "a different period, spelled out"),
    ("Meine Kündigungsfrist beträgt drei Monate zum Monatsende.", "a different period end"),
    ("Meine Kündigungsfrist beträgt drei Monate zum Quartalsende, frei bin ich ab dem 15. Januar.",
     "a date nobody typed beside the typed answer"),
    ("Meine Kündigungsfrist beträgt drei Monate zum Quartalsende plus zwei Wochen.",
     "a count beside the typed answer that the answer does not state"),
    ("Die dauerhafte Führung von rund 250 Mitarbeitenden wäre für mich der nächste Schritt.",
     "a fabricated figure in the form's own wording"),
    ("Die dauerhafte Führung von rund 120 Mitarbeitenden an fünf Standorten wäre für mich der nächste Schritt.",
     "a count nobody typed or recorded"),
])
@pytest.mark.asyncio
async def test_a_fact_the_form_does_not_state_is_never_marked(sentence, why):
    stub = _Stub(judge=True)  # even a judge that grants everything is not asked
    v = await _audit(sentence, provider=stub)
    assert v.checker != "letter_form_input", why
    assert v.verdict in ("unbacked", "unverifiable"), why
    assert stub.judge_prompts == [] or all(sentence not in p for p in stub.judge_prompts), why


@pytest.mark.asyncio
async def test_the_fabricated_figure_beside_form_wording_stays_red():
    """Main's laundering negative, stated as the verdict: the 250 stays a red figure."""
    v = await _audit("Die dauerhafte Führung von rund 250 Mitarbeitenden wäre für mich der nächste Schritt.",
                     provider=_Stub(judge=True))
    assert (v.verdict, v.detail_key) == ("unbacked", "figuresNoEvidence")


# ── below the floor: the restatement judge, asked of the form text ─────────────


@pytest.mark.asyncio
async def test_the_judge_grants_the_mark_on_a_reworded_motivation():
    stub = _Stub(judge=True)
    v = await _audit(KATRIN_REWORDED, form=FORM_KATRIN, provider=stub)
    assert (v.verdict, v.checker, v.detail_params) == ("not_applicable", "letter_form_input", {"field": "motivation"})
    assert len(stub.judge_prompts) == 1
    prompt = stub.judge_prompts[0]
    assert "(mode: restatement)" in prompt
    assert f"CLAIM: {KATRIN_REWORDED}\nVAULT EVIDENCE:\n  [0] {FORM_KATRIN['motivation']}" in prompt


@pytest.mark.asyncio
async def test_a_judge_no_keeps_the_honest_unverifiable():
    v = await _audit(KATRIN_REWORDED, form=FORM_KATRIN, provider=_Stub(judge=False))
    assert (v.verdict, v.checker) == ("unverifiable", "grounding")


@pytest.mark.asyncio
async def test_no_judge_no_mark():
    v = await _audit(KATRIN_REWORDED, form=FORM_KATRIN, provider=None)
    assert (v.verdict, v.checker) == ("unverifiable", "grounding")


@pytest.mark.asyncio
async def test_the_judge_never_sees_a_red_sentence():
    """A sentence red for a figure the candidate DID type, below the floor: the
    deterministic rule does not reach it and the judge may not lift a red (ADR-068
    cl. 3)."""
    red = "Eine Führungsspanne von rund 120 Mitarbeitenden würde meine Verantwortung im Werk deutlich erweitern."
    stub = _Stub(judge=True)
    v = await _audit(red, provider=stub)
    assert (v.verdict, v.detail_key) == ("unbacked", "figuresNoEvidence")
    assert all(red not in p for p in stub.judge_prompts)


@pytest.mark.asyncio
async def test_a_judge_yes_without_a_verified_citation_grants_nothing():
    v = await _audit(KATRIN_REWORDED, form=FORM_KATRIN, provider=_Stub(judge=True, quote="ein Satz, den es nicht gibt"))
    assert (v.verdict, v.checker) == ("unverifiable", "grounding")


@pytest.mark.parametrize("sentence, why", [
    ("Bei der Hochland Verpackung GmbH moderierte ich Kaizen-Workshops zur Planung und zum Berichtswesen "
     "der Montage.", "the form covers a quarter of it"),
    ("Konzernabschluss interessiert mich.", "a one-word overlap"),
])
@pytest.mark.asyncio
async def test_a_sentence_the_form_barely_covers_is_not_judged(sentence, why):
    stub = _Stub(judge=True)
    v = await _audit(sentence, form=FORM_KATRIN, provider=stub)
    assert v.checker != "letter_form_input", why
    assert all(sentence not in p for p in stub.judge_prompts), why


@pytest.mark.asyncio
async def test_a_triage_exempt_sentence_below_the_floor_is_not_judged():
    stub = _Stub(judge=True, triage={KATRIN_REWORDED: "employer-fact"})
    v = await _audit(KATRIN_REWORDED, form=FORM_KATRIN, provider=stub)
    assert (v.verdict, v.checker) == ("not_applicable", "sentence_triage")
    assert stub.judge_prompts == []


# ── doors ─────────────────────────────────────────────────────────────────────


async def _seed_letter(db, form, *sentences):
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.models.job import JobAnalysis
    from tests.support.profile_factory import make_master_profile

    job_id, profile_id, cl_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db.add_all([
        JobAnalysis(
            id=job_id, raw_text_hash=f"fo-para-{cl_id}", raw_text="Leiter Operations", role_title="Leiter Operations",
            required_skills=[], nice_to_have_skills=[], keywords=[], seniority_level="senior",
            company_culture_signals=[], language_requirement="de",
        ),
        make_master_profile(id=profile_id, profile_json=json.loads(json.dumps(VAULT)),
                            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        GeneratedCoverLetter(
            id=cl_id, job_analysis_id=job_id, profile_id=profile_id, letter_data=_letter(*sentences),
            pre_gen_inputs=form, template="classic_german", status="ready", truthfulness_report=None,
            document_language="de", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        ),
    ])
    await db.commit()
    return cl_id


def _by_loc(report_dict):
    return {c["claim"]["location"]: c["verdict"] for c in report_dict["claims"]}


@pytest.mark.asyncio
async def test_door_rest_letter_self_audit_marks_the_paraphrase(db):  # noqa: F811
    """``_update_ats_report_letter`` (generation + section-edit re-audit): the
    spelled-out notice period by coverage, the reworded motivation by the judge
    through the door's own provider."""
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.services.cover_letter import _update_ats_report_letter

    cl_id = await _seed_letter(db, FORM_KATRIN, KATRIN_REWORDED, NOTICE_DE)
    cl = await db.get(GeneratedCoverLetter, cl_id)
    with patch("applire.services.cover_letter.get_provider", return_value=_Stub(judge=True)):
        await _update_ats_report_letter(cl, db, pdf=b"%PDF")
    db.expire_all()
    cl = await db.get(GeneratedCoverLetter, cl_id)
    got = _by_loc(cl.truthfulness_report)
    assert got["body.paragraphs[1][0]"]["checker"] == "letter_form_input"
    assert got["body.paragraphs[1][1]"]["checker"] == "letter_form_input"
    assert got["body.paragraphs[1][1]"]["detail_params"] == {"field": "availability"}


@pytest.mark.asyncio
async def test_door_mcp_stored_letter_audit_marks_the_paraphrase(db):  # noqa: F811
    from applire.mcp.server import mcp

    cl_id = await _seed_letter(db, FORM_KATRIN, KATRIN_REWORDED, NOTICE_DE)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=db)
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch("applire.mcp.server.get_db", return_value=cm), \
         patch("applire.mcp.server.get_provider", return_value=_Stub(judge=True)):
        out = await mcp.call_tool("audit_document", {"document_id": str(cl_id)})
    blocks = out[0] if isinstance(out, tuple) else out
    got = _by_loc(json.loads(blocks[0].text))
    assert got["body.paragraphs[1][0]"]["checker"] == "letter_form_input"
    assert got["body.paragraphs[1][1]"]["checker"] == "letter_form_input"
