# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""ADR-052 amended 2026-10-23 (ruling B3-9 = build-3 adversarial A-1, WP-FO).

Every certification and education ENTRY of a delivered CV is a claim of kind
``entry`` with a verdict — at every door: the generation self-audit, the
section-edit re-audit, the agent door's ``render_document`` (``render_agent_cv``)
and ``audit_document``. Before, ``extract_claims_from_tailored`` left both lists
out (the web pipeline copies them from the vault), so a fabricated
"Certified Public Accountant (CPA)" entry, a "Wirtschaftsprüfer-Examen" or a
"Dr. rer. pol." degree written through the agent door carried no verdict at all.
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

from applire.services.oracle.audit import audit_document, extract_claims_from_tailored
from applire.services.oracle.credentials import entry_backing

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

VAULT = {
    "personal_info": {"name": "Katrin Vogel"},
    "professional_summary": {"de": "Finanzbuchhalterin; enge Zusammenarbeit mit Wirtschaftsprüfern.", "en": ""},
    "work_experience": [{
        "id": "w1", "company": "Acme GmbH", "title": "Finanzbuchhalterin", "start_date": "2016-01",
        "responsibilities": ["Monatsabschlüsse nach HGB, Abstimmung mit den Wirtschaftsprüfern"],
    }],
    "education": [{"institution": "Hochschule Mainz", "degree": "Diplom-Kauffrau (FH)", "field": "BWL",
                   "degree_aliases": ["Dipl.-Kffr. (FH)"]}],
    "certifications": [
        {"name": "Industriemeister Metall (IHK)"},
        {"name": "Professional Scrum Master I (PSM I)"},
        {"name": "Master of Business Administration"},
        {"name": "Certified Public Accountant (CPA)", "status": "unconfirmed"},
    ],
    "skills": [{"name": "HGB"}],
}
_ENTRIES = (
    ("certifications[0].name", "Industriemeister Metall (IHK)"),
    ("certifications[1].name", "Professional Scrum Master I (PSM I)"),
    ("certifications[2].name", "Master of Business Administration"),
    ("education[0].degree", "Diplom-Kauffrau (FH)"),
    ("education[0].degree_aliases[0]", "Dipl.-Kffr. (FH)"),
    ("certifications[3].name", "Staplerschein (DGUV G 308)"),
    ("certifications[4].name", "ISO 9001"),
    ("certifications[5].name", "Certified Public Accountant (CPA)"),
    ("certifications[6].name", "Industriemeister Metall"),
    ("certifications[7].name", "Industriekaufmann"),
    ("certifications[8].name", "Handelsfachwirt (IHK)"),
    ("education[1].degree", "Promotion (Dr. rer. pol.)"),
    ("education[2].degree", "Master of Arts"),
)


@pytest.mark.parametrize("name, path", [
    ("Industriemeister Metall (IHK)", "certifications[0].name"),   # identity
    ("Industriemeister Metall", "certifications[0].name"),         # qualifier dropped
    ("Professional Scrum Master I (PSM I)®", "certifications[1].name"),  # mark stripped
    ("Scrum Master", "certifications[1].name"),                    # ≥2-token shortening
    ("MBA", "certifications[2].name"),                             # fixed abbreviation
    ("CPA", "certifications[5].name"),                             # the vault's own acronym
    ("Dr. rer. pol.", "education[1].degree"),                      # ditto, from a degree
    ("Diplom-Kauffrau (FH)", "education[0].degree"),
    ("Dipl.-Kffr. (FH)", "education[0].degree_aliases[0]"),        # ADR-046 alias
    ("Staplerschein", "certifications[3].name"),                   # one token, qualifier dropped
])
def test_a_vault_entry_backs_the_entry(name, path):
    b = entry_backing(name, _ENTRIES)
    assert (b.how is not None, b.path) == (True, path)


@pytest.mark.parametrize("name", [
    "Certified Public Accountant (CPA)",   # A-1's shape
    "Wirtschaftsprüfer-Examen",            # prose "Wirtschaftsprüfern" backs no entry
    "Dr. rer. pol.",
    "ISO 9001 Lead Auditor",               # LONGER than the vault's "ISO 9001": a new claim
    "Master",                              # a one-token run is no shortening
    "Professional Scrum Master II",        # a different level is not the vault's entry
])
def test_nothing_backs_the_entry(name):
    assert entry_backing(name, _ENTRIES[:5]).how is None


@pytest.mark.parametrize("name", [
    # Main's review of 84b8b229: a credential FAMILY groups different credentials.
    # Each of these shared a family with a vault entry and read grounded.
    "Industriemeister Elektrotechnik",      # vault: Industriemeister Metall
    "Bürokauffrau",                         # vault: Industriekaufmann (open family)
    "Steuerfachwirt (IHK)",                 # vault: Handelsfachwirt (IHK)
    "ACCA",                                 # vault: CPA — one acronym family, three bodies
    "Master of Science",                    # vault: Master of Arts
    "Wirtschaftsprüfer (IHK)",              # a chamber qualifier is no name of its own
    "Wirtschaftsprüfer (CPA)",              # main review of 03c6f117: a DOCUMENT bracket
                                            # never rides on the vault's "CPA"
])
def test_a_shared_family_is_not_the_same_entry(name):
    assert entry_backing(name, _ENTRIES).how is None


def test_entries_are_claims_of_their_own_kind():
    claims = extract_claims_from_tailored({
        "certifications": [{"name": "CPA"}, {"name": "  "}, "junk"],
        "education": [{"degree": "BA", "field": "VWL"}, {"degree": "", "institution": "TU"}],
    })
    assert [(c.location, c.text, c.kind) for c in claims] == [
        ("certifications[0]", "CPA", "entry"), ("education[0]", "BA", "entry"),
    ]


CV = {
    "contact": {"name": "Katrin Vogel"}, "summary": "", "work_history": [], "skills": [],
    "certifications": [
        {"name": "Industriemeister Metall"},
        {"name": "Certified Public Accountant (CPA)"},
    ],
    "education": [
        {"institution": "Hochschule Mainz", "degree": "Diplom-Kauffrau (FH)", "field": "Business Administration"},
        {"institution": "Universität Köln", "degree": "Dr. rer. pol.", "field": "VWL"},
    ],
}
_EXPECTED = {
    "certifications[0]": ("grounded", None),
    "certifications[1]": ("unbacked", "credentialUnbacked"),   # unconfirmed entry backs nothing
    "education[0]": ("grounded", None),                         # translated field: not the claim
    "education[1]": ("unbacked", "credentialUnbacked"),
}


def _entry_verdicts(claims):
    out = {}
    for c in claims:
        claim, verdict = (c["claim"], c["verdict"]) if isinstance(c, dict) else (c.claim.model_dump(), c.verdict.model_dump())
        if claim["kind"] == "entry":
            out[claim["location"]] = (verdict["verdict"], verdict.get("detail_key"))
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["de", "en"])
async def test_audit_document_grades_every_entry(lang):
    report = await audit_document("cv", VAULT, tailored_data=CV, provider=None, document_language=lang)
    assert _entry_verdicts(report.claims) == _EXPECTED
    unbacked = [c for c in report.claims if c.claim.location == "certifications[1]"][0]
    assert unbacked.verdict.detail_params == {"credential": "Certified Public Accountant (CPA)"}


@pytest.mark.asyncio
async def test_other_language_pair_leaves_an_unknown_name_open():
    """D2-1 guard: a translated entry name is not checked across languages; a
    known credential family is (the family table is language-neutral)."""
    cv = {"certifications": [{"name": "Gabelstaplerschein"}, {"name": "CPA"},
                             {"name": "Zertifizierte Datenschutzbeauftragte"},
                             {"name": "Industriekauffrau"}, {"name": "Kfz-Mechatronikermeister (HWK)"}]}
    vault = {**VAULT, "professional_summary": {"en": "Accountant working with auditors on closings.", "de": ""},
             "work_experience": [{**VAULT["work_experience"][0],
                                  "responsibilities": ["Monthly closings under HGB with the external auditors"]}]}
    report = await audit_document("cv", vault, tailored_data=cv, provider=None, document_language="de")
    assert _entry_verdicts(report.claims) == {
        "certifications[0]": ("unverifiable", None),
        "certifications[1]": ("unbacked", "credentialUnbacked"),
        # certified_x names a translatable phrase — not checked across languages
        "certifications[2]": ("unverifiable", None),
        # a German chamber/trade title the vault never names stays red (main
        # review of 03c6f117 — the guard must not fail open on these)
        "certifications[3]": ("unbacked", "credentialUnbacked"),
        "certifications[4]": ("unbacked", "credentialUnbacked"),
    }


# ── doors ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_door_generation_self_audit():
    from applire.services.oracle.selfaudit import build_self_audit_report

    report = await build_self_audit_report(VAULT, tailored_data=CV, provider=None)
    assert _entry_verdicts(report["claims"]) == _EXPECTED


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


@pytest_asyncio.fixture
async def seeded(db):
    from applire.models.job import JobAnalysis
    from tests.support.owners_3c import link_job
    from tests.support.profile_factory import make_master_profile

    job_id, profile_id = uuid.uuid4(), uuid.uuid4()
    db.add_all([
        JobAnalysis(
            id=job_id, raw_text_hash=f"fo-entry-{job_id}", raw_text="Leitung Rechnungswesen",
            role_title="Leitung Rechnungswesen", required_skills=["HGB"], nice_to_have_skills=[],
            keywords=["HGB"], seniority_level="senior", company_culture_signals=[],
            language_requirement="de",
        ),
        make_master_profile(
            id=profile_id, profile_json=VAULT,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
    ])
    await db.flush()
    await link_job(db, job_id)
    await db.commit()
    return {"db": db, "job_id": job_id, "profile_id": profile_id}


def _render_patches():
    return (
        patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")),
        patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"%PDF")),
        patch("applire.services.ats_audit.extract_text_and_pages", return_value=("Katrin Vogel HGB", 1)),
    )


@pytest.mark.asyncio
async def test_door_render_agent_cv(seeded):
    """The agent door's ``render_document`` persists agent-authored content
    verbatim — the entries are NOT copied from the vault here."""
    from applire.services.cv import render_agent_cv

    p1, p2, p3 = _render_patches()
    with p1, p2, p3:
        record = await render_agent_cv(dict(CV), seeded["job_id"], seeded["db"])
    assert _entry_verdicts(record.truthfulness_report["claims"]) == _EXPECTED


@pytest.mark.asyncio
async def test_door_section_edit_reaudit(seeded):
    """The CV section editor's background re-audit (``_update_ats_report_by_id``)
    re-grades the entries it never edits."""
    from fastapi import BackgroundTasks

    from applire.services.cv import render_agent_cv
    from applire.services.cv_section_editor import patch_cv_section

    db = seeded["db"]
    p1, p2, p3 = _render_patches()
    with p1, p2, p3:
        record = await render_agent_cv(dict(CV), seeded["job_id"], db)
        record.truthfulness_report = None
        await db.commit()
        bg = BackgroundTasks()
        await patch_cv_section(record.id, "introduction", "Finanzbuchhalterin mit HGB-Praxis.", False, db, bg)
        assert len(bg.tasks) == 1
        with patch("applire.services.cv.AsyncSessionLocal") as local:
            local.return_value.__aenter__.return_value = db
            await bg.tasks[0]()
    await db.refresh(record)
    assert record.truthfulness_report is not None
    assert _entry_verdicts(record.truthfulness_report["claims"]) == _EXPECTED


@pytest.mark.asyncio
async def test_door_mcp_audit_document(seeded):
    from applire.mcp.server import mcp
    from applire.models.cv import GeneratedCV

    db = seeded["db"]
    cv_id = uuid.uuid4()
    db.add(GeneratedCV(
        id=cv_id, job_analysis_id=seeded["job_id"], profile_id=seeded["profile_id"],
        tailored_data=CV, template="classic_german", status="ready", truthfulness_report=None,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    ))
    await db.commit()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=db)
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch("applire.mcp.server.get_db", return_value=cm), \
         patch("applire.mcp.server.get_provider", return_value=None):
        out = await mcp.call_tool("audit_document", {"document_id": str(cv_id)})
    blocks = out[0] if isinstance(out, tuple) else out
    assert _entry_verdicts(json.loads(blocks[0].text)["claims"]) == _EXPECTED
