# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""ADR-061/048 amended 2026-10-23 (ruling B3-10 = build-3 delivery F1, WP-FO).

What the candidate types into the letter form (motivation, notice period, salary)
is their own statement for THAT letter — the interview-answer principle. A letter
sentence built from it reads ``not_applicable`` / ``letter_form_input`` with the
neutral ``fromLetterForm`` detail: not red, no profile-write handle, nothing
reaches the vault. Delivery F1: Marcus typed "rund 120 Mitarbeitenden … etwa 90
Mitarbeitenden" into the motivation field, the writer restated it, and the
sentence went red ("The only vault evidence …", in English, in a German UI).

FO-2 = A: a figure the posting states is exempt only through this rule — a
writer sentence that adds a figure or a title nobody typed stays graded.
"""
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.services.oracle.audit import audit_document

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

_HOCH = "11111111-1111-1111-1111-111111111111"
_KUNST = "22222222-2222-2222-2222-222222222222"
VAULT = {
    "personal_info": {"name": "Marcus Weber"},
    "work_experience": [
        {"id": _HOCH, "company": "Hochland Verpackung GmbH", "title": "Produktionsleiter", "start_date": "2016-04",
         "responsibilities": ["Standortvertretung der Werkleitung für rund 90 Mitarbeitende im Dreischichtbetrieb"],
         "achievements": ["MES-Einführung von der Systemauswahl bis zum Rollout verantwortet"]},
        {"id": _KUNST, "company": "Kunststoffwerk Sauer AG", "title": "Schichtleiter", "start_date": "2010-01",
         "end_date": "2016-03", "responsibilities": ["Schichtführung Spritzguss mit 25 Mitarbeitenden"]},
    ],
    "skills": [{"name": "Lean Management"}, {"name": "MES"}],
}
FORM = {
    "tone": "formal", "salary": None,
    "motivation": ("Die dauerhafte Führungsspanne von rund 120 Mitarbeitenden wäre für mich der nächste Schritt "
                   "nach meiner Standortvertretung mit etwa 90 Mitarbeitenden, und die Verbindung von Lean und "
                   "Digitalisierung, wie ich sie bei der MES-Einführung erlebt habe, reizt mich."),
    "availability": "3 Monate zum Quartalsende",
    "recipient_name": None, "recipient_company": "Rheinwerk Verpackungen GmbH",
}
#: The 90 is owned by one position, the sentence names no employer and the letter
#: names two: the RESTATEMENT judge decides it (delivery F1's path).
F1_SENTENCE = ("Die dauerhafte Führungsspanne wäre für mich der nächste Schritt nach meiner Standortvertretung "
               "mit etwa 90 Mitarbeitenden.")
#: The posting's 120, typed by the candidate into the motivation field.
F1_POSTING_FIGURE = ("Die dauerhafte Führungsspanne von rund 120 Mitarbeitenden wäre für mich der nächste Schritt "
                     "nach meiner Standortvertretung mit etwa 90 Mitarbeitenden.")


def _letter(*sentences):
    return {"recipient": {"company": "Rheinwerk Verpackungen GmbH"}, "body": {"paragraphs": [
        "Bei der Hochland Verpackung GmbH und der Kunststoffwerk Sauer AG habe ich Produktion geführt.",
        " ".join(sentences),
    ]}}


def _at(report, i):
    return [c.verdict for c in report.claims if c.claim.location == f"body.paragraphs[1][{i}]"][0]


class _RedJudge:
    """Triage down (every sentence audited); the restatement judge says every
    candidate does NOT correspond — what turned F1's sentence red on delivery."""

    def __init__(self):
        self.prompts: list[str] = []

    async def aparse_json(self, prompt, system=None, **kw):
        from applire.prompts.oracle_judgement import ORACLE_JUDGEMENT_SYSTEM_PROMPT

        if system != ORACLE_JUDGEMENT_SYSTEM_PROMPT:
            raise RuntimeError("triage down")
        self.prompts.append(prompt)
        return {"items": [{"index": 0, "corresponds": False,
                           "vault_quote": VAULT["work_experience"][0]["responsibilities"][0]}]}


@pytest.mark.asyncio
async def test_f1_without_the_form_the_judge_turns_it_red():
    """The delivery shape, reproduced: the baseline this ruling changes."""
    report = await audit_document("cover_letter", VAULT, letter_data=_letter(F1_SENTENCE),
                                  provider=_RedJudge(), document_language="de")
    v = _at(report, 0)
    assert (v.verdict, v.checker, v.detail_key) == ("unbacked", "restatement_judgement", "restatementBorrowed")


@pytest.mark.asyncio
async def test_f1_with_the_form_is_the_candidates_own_statement():
    judge = _RedJudge()
    report = await audit_document("cover_letter", VAULT, letter_data=_letter(F1_SENTENCE),
                                  provider=judge, document_language="de", form_inputs=FORM)
    v = _at(report, 0)
    assert (v.verdict, v.checker, v.detail_key) == ("not_applicable", "letter_form_input", "fromLetterForm")
    assert v.detail_params == {"field": "motivation"}
    assert [(e.kind, e.ref) for e in v.evidence] == [("letter_form", "pre_gen_inputs.motivation")]
    assert judge.prompts == []  # the candidate left the batch (no judge call for it)
    assert report.counts.get("unbacked", 0) == 0


@pytest.mark.asyncio
async def test_a_posting_figure_the_candidate_typed_is_their_statement():
    without = await audit_document("cover_letter", VAULT, letter_data=_letter(F1_POSTING_FIGURE),
                                   provider=None, document_language="de")
    assert _at(without, 0).detail_key == "figuresNoEvidence"
    report = await audit_document("cover_letter", VAULT, letter_data=_letter(F1_POSTING_FIGURE),
                                  provider=None, document_language="de", form_inputs=FORM)
    assert _at(report, 0).checker == "letter_form_input"


@pytest.mark.asyncio
async def test_notice_period_sentence_is_marked_from_the_form():
    report = await audit_document("cover_letter", VAULT, letter_data=_letter(
        "Mein Eintrittstermin ist 3 Monate zum Quartalsende."), provider=None, document_language="de",
        form_inputs=FORM)
    v = _at(report, 0)
    assert (v.checker, v.detail_params) == ("letter_form_input", {"field": "availability"})


@pytest.mark.parametrize("sentence, why", [
    ("Die dauerhafte Führungsspanne von rund 150 Mitarbeitenden wäre für mich der nächste Schritt nach "
     "meiner Standortvertretung mit etwa 90 Mitarbeitenden.", "a figure nobody typed (FO-2 = A)"),
    ("Als Bilanzbuchhalter wäre die dauerhafte Führungsspanne von rund 120 Mitarbeitenden für mich der "
     "nächste Schritt nach meiner Standortvertretung.", "a title nobody typed"),
    ("Nach ISO 50001 wäre die dauerhafte Führungsspanne von rund 120 Mitarbeitenden für mich der nächste "
     "Schritt nach meiner Standortvertretung.", "an identifier nobody typed"),
    ("Ich habe 25 Jahre Erfahrung in der Produktionsleitung.", "nothing in the form says it"),
])
@pytest.mark.asyncio
async def test_what_nobody_typed_stays_graded(sentence, why):
    form = {**FORM, "motivation": FORM["motivation"] + " Ich habe Erfahrung in der Produktionsleitung."}
    report = await audit_document("cover_letter", VAULT, letter_data=_letter(sentence), provider=None,
                                  document_language="de", form_inputs=form)
    assert _at(report, 0).checker != "letter_form_input", why


@pytest.mark.asyncio
async def test_a_stated_duration_the_vault_contradicts_stands():
    """A tenure flag is a contradiction with the dated record, not an absence."""
    form = {**FORM, "motivation": "Mit 25 Jahren Erfahrung in der Produktionsleitung suche ich den nächsten Schritt."}
    report = await audit_document("cover_letter", VAULT, letter_data=_letter(
        "Mit 25 Jahren Erfahrung in der Produktionsleitung suche ich den nächsten Schritt."), provider=None,
        document_language="de", form_inputs=form)
    v = _at(report, 0)
    assert v.verdict == "unbacked" and (v.detail_key or "").startswith(("tenure", "subjectTenure"))


@pytest.mark.asyncio
async def test_a_vault_grounded_sentence_keeps_its_grounding():
    form = {**FORM, "motivation": "Die MES-Einführung von der Systemauswahl bis zum Rollout habe ich verantwortet."}
    report = await audit_document("cover_letter", VAULT, letter_data=_letter(
        "Bei der Hochland Verpackung GmbH habe ich die MES-Einführung von der Systemauswahl bis zum Rollout "
        "verantwortet."), provider=None, document_language="de", form_inputs=form)
    assert _at(report, 0).verdict == "grounded"


@pytest.mark.asyncio
async def test_only_statement_fields_count():
    """The recipient fields and the tone are not statements about the candidate."""
    form = {"recipient_company": "Rheinwerk Verpackungen GmbH führt 120 Mitarbeitende in drei Werken",
            "tone": "Rheinwerk Verpackungen führt 120 Mitarbeitende"}
    report = await audit_document("cover_letter", VAULT, letter_data=_letter(
        "Ich führe 120 Mitarbeitende in drei Werken."), provider=None, document_language="de", form_inputs=form)
    assert _at(report, 0).checker != "letter_form_input"


@pytest.mark.asyncio
async def test_a_cv_never_reads_form_inputs():
    report = await audit_document("cv", VAULT, tailored_data={"summary": "Führungsspanne von rund 120 Mitarbeitenden."},
                                  provider=None, form_inputs=FORM)
    assert all(c.verdict.checker != "letter_form_input" for c in report.claims)


# ── doors ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_door_self_audit_threads_the_form():
    from applire.services.oracle.selfaudit import build_self_audit_report

    report = await build_self_audit_report(VAULT, letter_data=_letter(F1_SENTENCE), provider=None,
                                           document_language="de", form_inputs=FORM)
    checkers = {c["claim"]["location"]: c["verdict"]["checker"] for c in report["claims"]}
    assert checkers["body.paragraphs[1][0]"] == "letter_form_input"


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(db):
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.models.job import JobAnalysis
    from tests.support.profile_factory import make_master_profile

    job_id, profile_id, cl_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db.add_all([
        JobAnalysis(
            id=job_id, raw_text_hash=f"fo-form-{cl_id}", raw_text="Leiter Operations, 120 Mitarbeitende",
            role_title="Leiter Operations", required_skills=["Lean Management"], nice_to_have_skills=[],
            keywords=["Lean"], seniority_level="senior", company_culture_signals=[], language_requirement="de",
        ),
        make_master_profile(
            id=profile_id, profile_json=json.loads(json.dumps(VAULT)),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        GeneratedCoverLetter(
            id=cl_id, job_analysis_id=job_id, profile_id=profile_id, letter_data=_letter(F1_SENTENCE),
            pre_gen_inputs=FORM, template="classic_german", status="ready", truthfulness_report=None,
            document_language="de",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        ),
    ])
    await db.commit()
    return cl_id, profile_id


def _checker_at(report_dict, loc="body.paragraphs[1][0]"):
    return {c["claim"]["location"]: c["verdict"] for c in report_dict["claims"]}[loc]


@pytest.mark.asyncio
async def test_door_letter_generation_self_audit(db):
    """``_update_ats_report_letter`` — the generation and section-edit re-audit of a
    letter — hands the row's own ``pre_gen_inputs`` to the audit."""
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.models.profile import MasterProfile
    from applire.services.cover_letter import _update_ats_report_letter

    cl_id, profile_id = await _seed(db)
    cl = await db.get(GeneratedCoverLetter, cl_id)
    with patch("applire.services.cover_letter.get_provider", return_value=_RedJudge()):
        await _update_ats_report_letter(cl, db, pdf=b"%PDF")
    db.expire_all()
    cl = await db.get(GeneratedCoverLetter, cl_id)
    v = _checker_at(cl.truthfulness_report)
    assert (v["verdict"], v["checker"], v["detail_key"]) == ("not_applicable", "letter_form_input", "fromLetterForm")
    profile = await db.get(MasterProfile, profile_id)
    assert profile.profile_json == VAULT  # nothing reaches the vault


@pytest.mark.asyncio
async def test_door_mcp_audit_document(db):
    from applire.mcp.server import mcp

    cl_id, _ = await _seed(db)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=db)
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch("applire.mcp.server.get_db", return_value=cm), \
         patch("applire.mcp.server.get_provider", return_value=_RedJudge()):
        out = await mcp.call_tool("audit_document", {"document_id": str(cl_id)})
    blocks = out[0] if isinstance(out, tuple) else out
    v = _checker_at(json.loads(blocks[0].text))
    assert (v["verdict"], v["checker"]) == ("not_applicable", "letter_form_input")
