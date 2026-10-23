# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""ADR-052 amended 2026-10-23 (WP-FO): a rule above the pipeline that replaces a
deferred judgement candidate withdraws it from the batch.

Found reproducing the build-3 delivery F1: E-2's credential rule answers a
RESTATEMENT candidate with ``unbacked`` while the candidate stayed in
``audit_document``'s ``judgement_sink``. The batch then had one candidate more than
result slots — ``IndexError`` in ``audit_document`` (the self-audit leaves the report
NULL; the agent door answered an internal error) or a verdict patched onto the wrong
claim.

The shape below is real pipeline input, no monkeypatching: a letter naming two
employers, then two sentences naming none, each carrying a figure only one
position's evidence holds (two restatement candidates), the first of them ascribing
a title the vault never names (E-2 overrides it). Without the withdrawal every test
here fails.
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

_ACME = "11111111-1111-1111-1111-111111111111"
_BETA = "22222222-2222-2222-2222-222222222222"
VAULT = {
    "personal_info": {"name": "Katrin Vogel"},
    "work_experience": [
        {"id": _ACME, "company": "Acme GmbH", "title": "Finanzbuchhalterin", "start_date": "2018-01",
         "achievements": ["Monatsabschluss für 12 Gesellschaften nach HGB vorbereitet."]},
        {"id": _BETA, "company": "Beta AG", "title": "Buchhalterin", "start_date": "2014-01",
         "end_date": "2017-12", "achievements": ["Kreditorenbuchhaltung für 17 Standorte geführt."]},
    ],
}
LETTER = {
    "recipient": {"company": "Gamma SE"},
    "body": {"paragraphs": [
        "Bei der Acme GmbH und der Beta AG habe ich Buchhaltung verantwortet.",
        "Als Bilanzbuchhalterin habe ich Zahlenwerke für 12 Mandanten geprüft. "
        "Zuvor habe ich Lieferanten für 17 Regionen betreut.",
    ]},
}
_SECOND_UNIT = "Kreditorenbuchhaltung für 17 Standorte geführt."


class _JudgeSecond:
    """Triage unavailable (every sentence audited); the restatement judge answers
    the ONE candidate the batch should hold: ``corresponds=false`` citing the
    17-unit — so a verdict landing on the right claim is visible."""

    def __init__(self):
        self.batches: list[str] = []

    async def aparse_json(self, prompt, system=None, **kw):
        from applire.prompts.oracle_judgement import ORACLE_JUDGEMENT_SYSTEM_PROMPT

        if system != ORACLE_JUDGEMENT_SYSTEM_PROMPT:
            raise RuntimeError("triage down")
        self.batches.append(prompt)
        return {"items": [{"index": 0, "corresponds": False, "vault_quote": _SECOND_UNIT}]}


def _by_loc(report):
    return {c.claim.location: c.verdict for c in report.claims}


@pytest.mark.asyncio
async def test_overridden_candidate_does_not_break_the_audit_without_provider():
    report = await audit_document("cover_letter", VAULT, letter_data=LETTER, provider=None, document_language="de")
    v = _by_loc(report)
    assert v["body.paragraphs[1][0]"].detail_key == "credentialUnbacked"
    assert v["body.paragraphs[1][1]"].checker == "restatement_judgement"
    assert v["body.paragraphs[1][1]"].verdict == "unverifiable"  # fail-safe placeholder, its own


@pytest.mark.asyncio
async def test_the_judge_answers_the_remaining_candidate_on_its_own_claim():
    provider = _JudgeSecond()
    report = await audit_document("cover_letter", VAULT, letter_data=LETTER, provider=provider, document_language="de")
    v = _by_loc(report)
    assert len(provider.batches) == 1 and "12 Mandanten" not in provider.batches[0]
    assert v["body.paragraphs[1][0]"].detail_key == "credentialUnbacked"
    second = v["body.paragraphs[1][1]"]
    assert (second.verdict, second.checker) == ("unbacked", "restatement_judgement")
    assert second.detail_key == "restatementBorrowed"  # said in the UI language (B3-10 bug-fix part)


# ── the agent door, through mcp.call_tool (where it surfaced as an internal error)


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


async def _seed_letter(db, letter_data, pre_gen_inputs=None):
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.models.job import JobAnalysis
    from tests.support.profile_factory import make_master_profile

    job_id, profile_id, cl_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db.add_all([
        JobAnalysis(
            id=job_id, raw_text_hash=f"fo-{cl_id}", raw_text="Leitung Rechnungswesen",
            role_title="Leitung Rechnungswesen", required_skills=["HGB"], nice_to_have_skills=[],
            keywords=["HGB"], seniority_level="senior", company_culture_signals=[],
            language_requirement="de",
        ),
        make_master_profile(
            id=profile_id, profile_json=VAULT,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        GeneratedCoverLetter(
            id=cl_id, job_analysis_id=job_id, profile_id=profile_id,
            letter_data=letter_data, pre_gen_inputs=pre_gen_inputs or {},
            template="classic_german", status="ready", truthfulness_report=None,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        ),
    ])
    await db.commit()
    return cl_id


async def _door(db, document_id, provider):
    from applire.mcp.server import mcp

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=db)
    cm.__aexit__ = AsyncMock(return_value=False)
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.get_provider", return_value=provider),
    ):
        out = await mcp.call_tool("audit_document", {"document_id": str(document_id)})
    blocks = out[0] if isinstance(out, tuple) else out
    return json.loads(blocks[0].text)


@pytest.mark.asyncio
async def test_the_agent_door_audits_the_shape_instead_of_failing(db):
    cl_id = await _seed_letter(db, LETTER)
    report = await _door(db, cl_id, _JudgeSecond())
    by_loc = {c["claim"]["location"]: c["verdict"] for c in report["claims"]}
    assert by_loc["body.paragraphs[1][0]"]["detail_key"] == "credentialUnbacked"
    assert by_loc["body.paragraphs[1][1]"]["checker"] == "restatement_judgement"
    assert by_loc["body.paragraphs[1][1]"]["verdict"] == "unbacked"
