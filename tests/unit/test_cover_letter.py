"""
Sprint 25 — Cover Letter Generation (unit tests)
No Docker, no LLM, no external services.

Run:
    pytest tests/unit/test_cover_letter.py -v
"""
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


# ---------------------------------------------------------------------------
# Task 2 — TTL constants
# ---------------------------------------------------------------------------

def test_generated_documents_ttl_default():
    from applire.constants import GENERATED_DOCUMENTS_TTL_DAYS
    assert GENERATED_DOCUMENTS_TTL_DAYS == 90


def test_interview_session_ttl_default():
    from applire.constants import INTERVIEW_SESSION_TTL_DAYS
    assert INTERVIEW_SESSION_TTL_DAYS == 30


def test_upload_ttl_default():
    from applire.constants import UPLOAD_TTL_DAYS
    assert UPLOAD_TTL_DAYS == 7


def test_profile_inactivity_ttl_default():
    from applire.constants import PROFILE_INACTIVITY_TTL_DAYS
    assert PROFILE_INACTIVITY_TTL_DAYS == 730


# ---------------------------------------------------------------------------
# Task 3 — GeneratedCoverLetter model
# ---------------------------------------------------------------------------

import uuid
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def db():
    """In-memory SQLite session with all models registered."""
    from applire.db.session import Base
    import applire.models.user
    import applire.models.job
    import applire.models.profile
    import applire.models.gap
    import applire.models.cv
    import applire.models.cover_letter
    import applire.models.session
    import applire.models.application
    import applire.models.flow
    import applire.models.uploads

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_cover_letter_model_creates_with_defaults(db):
    from applire.models.cover_letter import GeneratedCoverLetter, CoverLetterStatus

    cl = GeneratedCoverLetter(
        job_analysis_id=uuid.uuid4(),
        profile_id=uuid.uuid4(),
        template="classic_german",
        letter_data={},
        pre_gen_inputs={},
    )
    db.add(cl)
    await db.commit()
    await db.refresh(cl)

    assert cl.id is not None
    assert cl.status == CoverLetterStatus.pending.value
    # SQLite returns offset-naive datetimes; strip tz for comparison
    expires = cl.expires_at.replace(tzinfo=None) if cl.expires_at.tzinfo else cl.expires_at
    assert expires > datetime.now()
    assert cl.deleted_at is None


@pytest.mark.asyncio
async def test_cover_letter_expires_at_is_90_days_out(db):
    from applire.models.cover_letter import GeneratedCoverLetter

    cl = GeneratedCoverLetter(
        job_analysis_id=uuid.uuid4(),
        profile_id=uuid.uuid4(),
        template="executive",
        letter_data={},
        pre_gen_inputs={},
    )
    db.add(cl)
    await db.commit()
    await db.refresh(cl)

    # SQLite returns offset-naive datetimes; strip tz for comparison
    expires = cl.expires_at.replace(tzinfo=None) if cl.expires_at.tzinfo else cl.expires_at
    delta = expires - datetime.now()
    assert 88 < delta.days <= 91


# ---------------------------------------------------------------------------
# Task 4 — FlowSession cover letter FK
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_flow_session_has_cover_letter_fk(db):
    from applire.models.flow import FlowSession
    import sqlalchemy as sa
    from applire.db.session import Base

    inspector = sa.inspect(Base.metadata.tables["flow_sessions"])
    col_names = [c.name for c in inspector.columns]
    assert "generated_cover_letter_id" in col_names


# ---------------------------------------------------------------------------
# Task 5 — Pydantic schemas
# ---------------------------------------------------------------------------

def test_cover_letter_generate_request_validates_tone():
    from applire.schemas.cover_letter import CoverLetterGenerateRequest
    req = CoverLetterGenerateRequest(job_id=uuid.uuid4(), tone="formal")
    assert req.tone == "formal"


def test_cover_letter_generate_request_rejects_invalid_tone():
    from pydantic import ValidationError
    from applire.schemas.cover_letter import CoverLetterGenerateRequest
    with pytest.raises(ValidationError):
        CoverLetterGenerateRequest(job_id=uuid.uuid4(), tone="aggressive")


def test_flow_state_response_has_cover_letter_summary_field():
    from applire.schemas.flow import FlowStateResponse
    fields = FlowStateResponse.model_fields
    assert "cover_letter_summary" in fields


# ---------------------------------------------------------------------------
# Task 6 — Recipient extraction
# ---------------------------------------------------------------------------

def test_extract_recipient_finds_anrede_pattern():
    from applire.utils.recipient_extraction import extract_recipient_from_jd
    jd = "Bitte richten Sie Ihre Bewerbung an Dr. Sarah Müller, HR-Abteilung."
    result = extract_recipient_from_jd(jd)
    assert result["name"] == "Dr. Sarah Müller"


def test_extract_recipient_finds_english_pattern():
    from applire.utils.recipient_extraction import extract_recipient_from_jd
    jd = "Please address your application to Ms. Anna Schmidt, Talent Acquisition."
    result = extract_recipient_from_jd(jd)
    assert result["name"] == "Ms. Anna Schmidt"


def test_extract_recipient_returns_none_when_not_found():
    from applire.utils.recipient_extraction import extract_recipient_from_jd
    result = extract_recipient_from_jd("We are looking for a senior engineer.")
    assert result["name"] is None


# #272 Task 5 — LinkedIn "Direct message the job poster" block has no pattern
# (RC-F ground truth): the scrape duplicates the short form before the full
# name; the full name is whatever immediately precedes " | ".


def test_extract_recipient_finds_linkedin_job_poster_block():
    from applire.utils.recipient_extraction import extract_recipient_from_jd

    jd = (
        "Direct message the job poster from Connect-AI Sean Michael M. Sean "
        "Michael M. Sean Murphy | Principal AI, Data & Software Engineering "
        "Specialist"
    )
    result = extract_recipient_from_jd(jd)
    assert result["name"] == "Sean Murphy"


def test_extract_recipient_linkedin_pattern_ignores_unrelated_pipe():
    """Negative case: a pipe elsewhere in the JD with no 'Direct message the job
    poster' anchor must never fire this pattern."""
    from applire.utils.recipient_extraction import extract_recipient_from_jd

    jd = "Tech Stack: Python | FastAPI | PostgreSQL. We are hiring a Backend Engineer."
    result = extract_recipient_from_jd(jd)
    assert result["name"] is None


def test_extract_recipient_linkedin_pattern_requires_anchor_phrase():
    """A bare '<Name> | <Title>' shape without the LinkedIn anchor phrase must
    not be mistaken for the job-poster block."""
    from applire.utils.recipient_extraction import extract_recipient_from_jd

    jd = "John Appleseed | Senior Recruiter at Some Company. Apply within."
    result = extract_recipient_from_jd(jd)
    assert result["name"] is None


# ---------------------------------------------------------------------------
# Task 7 — LLM prompt builder
# ---------------------------------------------------------------------------

def test_build_cover_letter_prompt_includes_salary():
    from applire.prompts.cover_letter import build_cover_letter_prompt
    prompt = build_cover_letter_prompt(
        cv_data={"contact": {"name": "Marcus Bauer"}, "summary": "QA expert"},
        jd_text="We are hiring a QA Manager at Roche Diagnostics.",
        pre_gen_inputs={"salary": "95.000 €", "tone": "formal"},
        detected_language="de",
    )
    assert "Gehaltswunsch" in prompt
    assert "95.000 €" in prompt


def test_build_cover_letter_prompt_includes_availability():
    from applire.prompts.cover_letter import build_cover_letter_prompt
    prompt = build_cover_letter_prompt(
        cv_data={"contact": {"name": "Marcus Bauer"}, "summary": "QA expert"},
        jd_text="We are hiring a QA Manager.",
        pre_gen_inputs={"availability": "3 months notice", "tone": "professional"},
        detected_language="en",
    )
    assert "3 months notice" in prompt


def test_build_cover_letter_prompt_includes_role_title():
    """F3 (blind PQ blocker) — the letter targeted the candidate's CURRENT title
    ("Head of IT Quality Systems") instead of the job's role
    ("Head of Computerized Systems Validation (CSV/CSA)") because the prompt never
    received the target role at all. The role title must be injected as an explicit
    fact, with wording that ties it to "this role" so the LLM cannot confuse it with
    a title mentioned in the candidate profile."""
    from applire.prompts.cover_letter import build_cover_letter_prompt

    prompt = build_cover_letter_prompt(
        cv_data={
            "contact": {"name": "Marcus Bauer"},
            "summary": "Head of IT Quality Systems with 10 years in pharma.",
        },
        jd_text="We are hiring for a senior validation role.",
        pre_gen_inputs={"tone": "formal", "recipient_company": "Helvia Pharma Services GmbH"},
        detected_language="en",
        role_title="Head of Computerized Systems Validation (CSV/CSA)",
    )
    assert "Head of Computerized Systems Validation (CSV/CSA)" in prompt
    # Must be framed as the TARGET role for THIS application, not just dropped in.
    low = prompt.lower()
    assert "target role" in low or "applying for" in low or "this role" in low


def test_build_cover_letter_prompt_role_title_optional():
    """role_title is optional (legacy callers / missing job data) — must not raise."""
    from applire.prompts.cover_letter import build_cover_letter_prompt

    prompt = build_cover_letter_prompt(
        cv_data={"contact": {"name": "A. Test"}, "summary": "Engineer"},
        jd_text="Test JD",
        pre_gen_inputs={"tone": "formal"},
        detected_language="de",
    )
    assert isinstance(prompt, str)


def test_cover_letter_prompt_carries_word_budget_line():
    """#177 / ADR-051 §6 amended: the feedforward word budget (from REGION_NORMS)
    reaches the LLM as an explicit instruction, before the JD block. Review
    Finding 3 / ADR-051 §1: the page norm is interpolated from letter_pages,
    never a hard-coded literal in the prompt text."""
    from applire.prompts.cover_letter import build_cover_letter_prompt

    prompt = build_cover_letter_prompt(
        cv_data={}, jd_text="x", pre_gen_inputs={},
        detected_language="de", word_budget=300, letter_pages=1,
    )
    assert "300" in prompt and "WORD BUDGET" in prompt
    assert "1-page" in prompt


def test_cover_letter_prompt_word_budget_line_omits_page_norm_when_letter_pages_not_given():
    """letter_pages is optional (legacy/degraded callers) — the WORD BUDGET line
    still renders, just without a page-count claim."""
    from applire.prompts.cover_letter import build_cover_letter_prompt

    prompt = build_cover_letter_prompt(
        cv_data={}, jd_text="x", pre_gen_inputs={},
        detected_language="de", word_budget=300,
    )
    assert "WORD BUDGET" in prompt
    assert "region's page norm" in prompt


def test_cover_letter_prompt_omits_word_budget_line_when_not_given():
    """word_budget is optional — legacy/degraded callers must not break."""
    from applire.prompts.cover_letter import build_cover_letter_prompt

    prompt = build_cover_letter_prompt(
        cv_data={}, jd_text="x", pre_gen_inputs={}, detected_language="de",
    )
    assert "WORD BUDGET" not in prompt


def test_build_condense_prompt_carries_budget_page_count_and_json():
    """build_condense_prompt (#177): one bounded condense-regenerate — same JSON
    shape, same facts, fewer words. Must not invite new claims. Review Finding 3
    / ADR-051 §1: letter_pages is a required, caller-supplied norm value — never
    a hard-coded literal in the prompt text."""
    from applire.prompts.cover_letter import build_condense_prompt

    letter_data = {"body": {"paragraphs": ["Hello there."]}}
    prompt = build_condense_prompt(letter_data, word_budget=300, page_count=2, letter_pages=1)
    assert "300" in prompt
    assert "2 pages" in prompt
    assert "1 page" in prompt
    assert "Hello there." in prompt
    assert "NEVER add new facts" in prompt


def test_build_condense_prompt_pluralizes_multi_page_norm():
    """A region whose letter_pages norm is >1 must not read '1 page(s)' — the
    pluralization must follow the interpolated value, not a hard-coded literal."""
    from applire.prompts.cover_letter import build_condense_prompt

    letter_data = {"body": {"paragraphs": ["Hello there."]}}
    prompt = build_condense_prompt(letter_data, word_budget=600, page_count=3, letter_pages=2)
    assert "2 pages" in prompt
    assert "1 page" not in prompt


def test_build_cover_letter_prompt_returns_system_and_user():
    from applire.prompts.cover_letter import build_cover_letter_prompt, SYSTEM_PROMPT
    prompt = build_cover_letter_prompt(
        cv_data={"contact": {"name": "A. Test"}, "summary": "Engineer"},
        jd_text="Test JD",
        pre_gen_inputs={"tone": "conversational"},
        detected_language="de",
    )
    assert isinstance(SYSTEM_PROMPT, str)
    assert len(prompt) > 100


def test_system_prompt_states_grounding_contract():
    """US170 (JF-M-8.1) — the letter goes out signed, so the body must contain only
    claims grounded in the candidate's data. The prompt is the prevention tier: it must
    forbid inventing facts and name the high-risk classes (dates, employers, achievements)."""
    from applire.prompts.cover_letter import SYSTEM_PROMPT

    low = SYSTEM_PROMPT.lower()
    # must explicitly forbid invention/fabrication
    assert "invent" in low or "fabricat" in low
    # must name the high-risk fact classes the FMEA observed
    assert "date" in low
    assert "employ" in low or "company" in low
    assert "achievement" in low
    # claims must be tied back to the provided candidate data, not the JD's wish-list
    assert "candidate profile" in low or "candidate data" in low


def test_system_prompt_forbids_claiming_gaps_in_possessive_framing():
    """E037 PQ #1 (HARD blocker) — for a weak-fit job the letter wrote
    'I ... have a proven track record in incident management and cloud platform
    engineering', both honest GAPS. The CV stayed honest because CV Rule 3 forbids
    incorporating a keyword gap unless explicitly demonstrated. The cover-letter
    SYSTEM_PROMPT must carry the equivalent hard rule: a competency/skill/track-record
    may be asserted ONLY where it traces to a profile bullet or a CLAIMABLE ledger
    entry, and JD requirement terms / DO-NOT-CLAIM concepts must NEVER appear in a
    possessive 'have / proven track record in / expertise in' framing — only motivation."""
    from applire.prompts.cover_letter import SYSTEM_PROMPT

    low = SYSTEM_PROMPT.lower()
    # the exact failure phrasing class must be named and forbidden
    assert "track record" in low
    assert "proven track record" in low
    # the enumerated forbidden possessive framings (at least these representative ones)
    assert "expertise in" in low
    assert "experienced in" in low
    # a claim must trace to a profile bullet / claimable ledger evidence
    assert "bullet" in low
    assert "claimable" in low
    # DO-NOT-CLAIM concepts must be named as never-claimable
    assert "do-not-claim" in low or "do not claim" in low
    # the only permitted framing for a non-evidenced requirement is motivation/eagerness
    assert "motivation" in low or "grow into" in low or "eager" in low


# ---------------------------------------------------------------------------
# E048/US264 (ADR-057 amended 2026-07-24) — positioning inputs
# ---------------------------------------------------------------------------


def test_system_prompt_states_positioning_inputs_contract():
    """ADR-057 amended 2026-07-24 — the SYSTEM_PROMPT must carry explicit instructions
    for all three positioning inputs, each gated on grounding."""
    from applire.prompts.cover_letter import SYSTEM_PROMPT

    low = SYSTEM_PROMPT.lower()
    assert "positioning" in low
    assert "company & domain engagement" in low or "company and domain engagement" in low
    assert "transfer argument" in low
    assert "availability" in low and "concurrent commitments" in low


# ---------------------------------------------------------------------------
# #272 Task 2 — the closing paragraph is REQUIRED content
# ---------------------------------------------------------------------------


def test_system_prompt_requires_a_genuine_closing_paragraph():
    """RC-D ground truth: the run-5 letter's real closing ("I would welcome the
    opportunity to discuss how my experience aligns with your needs. My notice
    period can be discussed.") was eroded by the review loop down to the bare
    stub "Notice period can be discussed." The writer's own SYSTEM_PROMPT must
    state the closing paragraph is required content and that availability is
    folded into it, never left standalone."""
    from applire.prompts.cover_letter import SYSTEM_PROMPT

    low = SYSTEM_PROMPT.lower()
    assert "closing paragraph" in low
    assert "required" in low
    assert "call to action" in low or "call-to-action" in low
    # availability must be folded into the closing, never a standalone line
    assert "never" in low and (
        "standalone" in low or "terminal line" in low or "bare" in low
    )


def test_build_cover_letter_prompt_includes_company_engagement_block():
    from applire.prompts.cover_letter import build_cover_letter_prompt

    prompt = build_cover_letter_prompt(
        cv_data={"contact": {"name": "A. Test"}},
        jd_text="We build diagnostics instruments for regulated healthcare labs.",
        pre_gen_inputs={},
        detected_language="en",
        company_name="Roche Diagnostics",
    )
    assert "POSITIONING: COMPANY & DOMAIN ENGAGEMENT" in prompt
    assert "TARGET COMPANY: Roche Diagnostics" in prompt


def test_build_cover_letter_prompt_omits_company_engagement_block_when_no_company_name():
    from applire.prompts.cover_letter import build_cover_letter_prompt

    prompt = build_cover_letter_prompt(
        cv_data={}, jd_text="x", pre_gen_inputs={}, detected_language="en",
    )
    assert "POSITIONING: COMPANY & DOMAIN ENGAGEMENT" not in prompt


def test_build_cover_letter_prompt_includes_gap_testimony_verbatim():
    from applire.prompts.cover_letter import build_cover_letter_prompt

    prompt = build_cover_letter_prompt(
        cv_data={}, jd_text="x", pre_gen_inputs={}, detected_language="en",
        gap_testimony={
            "gap": "regulated industries experience",
            "story": {
                "challenge": "New to regulated industries.",
                "mechanism": "Applied adjacent QA rigor from my prior role.",
                "outcome": "Delivered a compliant release on the first attempt.",
                "benchmark": None,
            },
        },
    )
    assert "POSITIONING: HONEST GAP / TRANSFER ARGUMENT" in prompt
    assert "GAP: regulated industries experience" in prompt
    assert "Applied adjacent QA rigor from my prior role." in prompt
    assert "Delivered a compliant release on the first attempt." in prompt


def test_build_cover_letter_prompt_omits_gap_testimony_block_when_none():
    from applire.prompts.cover_letter import build_cover_letter_prompt

    prompt = build_cover_letter_prompt(
        cv_data={}, jd_text="x", pre_gen_inputs={}, detected_language="en",
    )
    assert "POSITIONING: HONEST GAP / TRANSFER ARGUMENT" not in prompt


def test_build_cover_letter_prompt_includes_availability_testimony_verbatim():
    from applire.prompts.cover_letter import build_cover_letter_prompt

    prompt = build_cover_letter_prompt(
        cv_data={}, jd_text="x", pre_gen_inputs={}, detected_language="en",
        availability_testimony="I run two advisory roles in parallel and block dedicated hours for each.",
    )
    assert "POSITIONING: AVAILABILITY / CONCURRENT COMMITMENTS" in prompt
    assert "I run two advisory roles in parallel" in prompt


def test_build_cover_letter_prompt_omits_availability_block_when_none():
    from applire.prompts.cover_letter import build_cover_letter_prompt

    prompt = build_cover_letter_prompt(
        cv_data={}, jd_text="x", pre_gen_inputs={}, detected_language="en",
    )
    assert "POSITIONING: AVAILABILITY / CONCURRENT COMMITMENTS" not in prompt


def test_build_cover_letter_prompt_routes_gap_to_do_not_claim_and_grounds_profile():
    """E037 PQ #1 — build_cover_letter_prompt must (a) feed grounded profile material
    (real work-history bullets, not just the thin summary) so achievements come from
    real history, and (b) route a not-claimable ledger concept under DO NOT CLAIM and
    NEVER list it among the claimable keywords."""
    from applire.prompts.cover_letter import build_cover_letter_prompt

    cv_data = {
        "contact": {"name": "Marcus Bauer"},
        "summary": "QA specialist",
        "skills": ["Test Automation", "Selenium"],
        "work_history": [
            {
                "role": "QA Engineer",
                "company": "Roche",
                "start_date": "2019",
                "end_date": "2024",
                "bullets": [
                    "Built automated regression suites for diagnostics software",
                    "Led test strategy for release pipelines",
                ],
            }
        ],
    }
    ledger = [
        {
            "concept": "Test Automation",
            "surface_forms": ["Test Automation"],
            "claimable": True,
            "status": "direct",
            "sources": ["required"],
            "fit_weight": 1.0,
            "evidence": "Built automated regression suites",
        },
        {
            "concept": "Incident Management",
            "surface_forms": ["Incident Management"],
            "claimable": False,
            "status": "gap",
            "sources": ["required"],
            "fit_weight": 1.0,
            "evidence": "",
        },
    ]
    prompt = build_cover_letter_prompt(
        cv_data=cv_data,
        jd_text="We need incident management and cloud platform engineering experience.",
        pre_gen_inputs={"tone": "formal"},
        detected_language="en",
        keyword_ledger=ledger,
    )

    # (a) grounded profile material present — a real bullet, not only the summary
    assert "Built automated regression suites for diagnostics software" in prompt

    # (b) gap concept routed under DO NOT CLAIM, not listed as claimable
    claimable_start = prompt.index("CLAIMABLE (supported")
    do_not_claim_idx = prompt.index("DO NOT CLAIM", claimable_start)
    claimable_listing = prompt[claimable_start:do_not_claim_idx]
    assert "Test Automation" in claimable_listing
    assert "Incident Management" not in claimable_listing
    assert "Incident Management" in prompt[do_not_claim_idx:]


# ---------------------------------------------------------------------------
# Task 8 — Generation service
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_cover_letter_creates_pending_record(db):
    """generate_cover_letter should create a GeneratedCoverLetter with status=pending."""
    from unittest.mock import AsyncMock, MagicMock
    from applire.models.cover_letter import GeneratedCoverLetter, CoverLetterStatus
    from applire.models.cv import GeneratedCV
    from applire.models.flow import FlowSession
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.user import User
    from applire.schemas.cover_letter import CoverLetterGenerateRequest

    # Seed minimal DB records
    user = User(id=uuid.uuid4(), email="test@test.com")
    db.add(user)
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="abc123",
        raw_text="QA Manager at Roche",
        role_title="QA Manager",
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="de",
    )
    db.add(job)
    profile = MasterProfile(profile_json={
        "contact": {"name": "Marcus Bauer", "email": "m@test.com"},
        "summary": "QA Expert",
        "work_history": [],
        "skills": [],
        "education": [],
        "languages": [],
    })
    db.add(profile)
    await db.flush()

    cv = GeneratedCV(
        job_analysis_id=job.id,
        profile_id=profile.id,
        tailored_data={
            "contact": {"name": "Marcus Bauer", "email": "m@test.com"},
            "summary": "QA Expert",
            "work_history": [],
            "skills": [],
            "education": [],
            "languages": [],
        },
        template="executive",
        status="ready",
    )
    db.add(cv)

    flow = FlowSession(
        user_id=user.id,
        job_id=job.id,
        generated_cv_id=cv.id,
        available_actions={},
    )
    db.add(flow)
    await db.commit()

    # Mock BackgroundTasks and LLM provider
    bg = MagicMock()
    bg.add_task = MagicMock()
    mock_provider = AsyncMock()

    from applire.services.cover_letter import generate_cover_letter
    request = CoverLetterGenerateRequest(
        job_id=job.id,
        tone="formal",
    )
    response = await generate_cover_letter(request, db, mock_provider, bg, "http://localhost:8001")

    assert response.cover_letter_id is not None
    assert response.status == CoverLetterStatus.pending
    bg.add_task.assert_called_once()

    # FlowSession should be updated
    await db.refresh(flow)
    assert flow.generated_cover_letter_id == response.cover_letter_id


@pytest.mark.asyncio
async def test_generate_cover_letter_renders_inline_when_no_background_tasks(db):
    """#170: the MCP/agent channel has no FastAPI request lifecycle to defer
    to (unlike REST, which always passes a real BackgroundTasks), so
    background_tasks must be optional and, when omitted, the render must run
    inline before the call returns — mirroring services/cv.py:generate_cv."""
    from unittest.mock import AsyncMock, patch
    from applire.models.cover_letter import CoverLetterStatus
    from applire.models.flow import FlowSession
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.user import User
    from applire.schemas.cover_letter import CoverLetterGenerateRequest

    user = User(id=uuid.uuid4(), email="test2@test.com")
    db.add(user)
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="def456",
        raw_text="QA Manager at Roche",
        role_title="QA Manager",
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="de",
    )
    db.add(job)
    profile = MasterProfile(profile_json={
        "contact": {"name": "Marcus Bauer", "email": "m@test.com"},
        "summary": "QA Expert",
        "work_history": [],
        "skills": [],
        "education": [],
        "languages": [],
    })
    db.add(profile)
    await db.flush()

    flow = FlowSession(
        user_id=user.id,
        job_id=job.id,
        generated_cv_id=None,
        available_actions={},
    )
    db.add(flow)
    await db.commit()

    mock_provider = AsyncMock()

    async def _fake_render_inline(cl_id, cv_id, job_id):
        # Stand-in for the real LLM+Jinja2+Playwright render — flips the
        # record to a terminal status, same observable effect the agent
        # relies on (get_cover_letter_status/generate_cover_letter's own
        # return value must both see it without a second poll).
        from applire.models.cover_letter import GeneratedCoverLetter
        rec = await db.get(GeneratedCoverLetter, cl_id)
        rec.status = CoverLetterStatus.ready.value
        await db.commit()

    from applire.services.cover_letter import generate_cover_letter
    request = CoverLetterGenerateRequest(job_id=job.id, tone="formal")

    with patch(
        "applire.services.cover_letter._render_cover_letter_background",
        AsyncMock(side_effect=_fake_render_inline),
    ) as mock_render:
        response = await generate_cover_letter(
            request, db, mock_provider, base_url="http://localhost:8001"
        )

    mock_render.assert_awaited_once()
    assert response.status == CoverLetterStatus.ready, (
        "generate_cover_letter must reflect the post-render status when it "
        "rendered inline, not the stale 'pending' it wrote before rendering"
    )


# ---------------------------------------------------------------------------
# Task 8 (continued) — service helper functions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_cover_letter_status_pending(db):
    from applire.models.cover_letter import GeneratedCoverLetter, CoverLetterStatus
    from applire.services.cover_letter import get_cover_letter_status

    cl = GeneratedCoverLetter(
        job_analysis_id=uuid.uuid4(),
        profile_id=uuid.uuid4(),
        template="classic_german",
        letter_data={},
        pre_gen_inputs={},
        status=CoverLetterStatus.pending.value,
    )
    db.add(cl)
    await db.commit()
    await db.refresh(cl)

    result = await get_cover_letter_status(cl.id, db, "http://localhost:8001")
    assert result.status == CoverLetterStatus.pending.value
    assert result.html_url is None  # not ready yet


@pytest.mark.asyncio
async def test_get_cover_letter_status_ready_has_urls(db):
    from applire.models.cover_letter import GeneratedCoverLetter, CoverLetterStatus
    from applire.services.cover_letter import get_cover_letter_status

    cl = GeneratedCoverLetter(
        job_analysis_id=uuid.uuid4(),
        profile_id=uuid.uuid4(),
        template="executive",
        letter_data={"body": {"paragraphs": ["Hello"]}},
        pre_gen_inputs={},
        status=CoverLetterStatus.ready.value,
    )
    db.add(cl)
    await db.commit()
    await db.refresh(cl)

    result = await get_cover_letter_status(cl.id, db, "http://localhost:8001")
    assert result.status == CoverLetterStatus.ready.value
    assert result.html_url is not None
    assert result.pdf_url is not None


@pytest.mark.asyncio
async def test_get_cover_letter_status_not_found(db):
    from applire.services.cover_letter import get_cover_letter_status

    with pytest.raises(LookupError):
        await get_cover_letter_status(uuid.uuid4(), db, "http://localhost:8001")


@pytest.mark.asyncio
async def test_patch_cover_letter_section_body(db):
    from applire.models.cover_letter import GeneratedCoverLetter, CoverLetterStatus
    from applire.services.cover_letter import patch_cover_letter_section

    cl = GeneratedCoverLetter(
        job_analysis_id=uuid.uuid4(),
        profile_id=uuid.uuid4(),
        template="classic_german",
        letter_data={"body": {"paragraphs": ["original"]}},
        pre_gen_inputs={},
        status=CoverLetterStatus.ready.value,
    )
    db.add(cl)
    await db.commit()
    await db.refresh(cl)

    await patch_cover_letter_section(cl.id, "body", "Updated body text.", db)
    await db.refresh(cl)
    assert cl.section_overrides is not None
    assert cl.section_overrides["body"] == "Updated body text."


@pytest.mark.asyncio
async def test_patch_cover_letter_section_not_found(db):
    from applire.services.cover_letter import patch_cover_letter_section

    with pytest.raises(LookupError):
        await patch_cover_letter_section(uuid.uuid4(), "body", "text", db)


@pytest.mark.asyncio
async def test_get_cover_letter_by_job_not_found(db):
    from applire.services.cover_letter import get_cover_letter_by_job

    with pytest.raises(LookupError):
        await get_cover_letter_by_job(uuid.uuid4(), db, "http://localhost:8001")


@pytest.mark.asyncio
async def test_get_cover_letter_by_job_returns_status(db):
    from applire.models.cover_letter import GeneratedCoverLetter, CoverLetterStatus
    from applire.models.flow import FlowSession
    from applire.models.user import User
    from applire.models.job import JobAnalysis
    from applire.services.cover_letter import get_cover_letter_by_job

    user = User(id=uuid.uuid4(), email="byj@test.com")
    db.add(user)
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="xyz789",
        raw_text="Engineer role",
        role_title="Engineer",
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="mid",
        company_culture_signals=[],
        language_requirement="de",
    )
    db.add(job)
    cl = GeneratedCoverLetter(
        job_analysis_id=job.id,
        profile_id=uuid.uuid4(),
        template="executive",
        letter_data={},
        pre_gen_inputs={},
        status=CoverLetterStatus.pending.value,
    )
    db.add(cl)
    await db.flush()

    flow = FlowSession(
        user_id=user.id,
        job_id=job.id,
        generated_cover_letter_id=cl.id,
        available_actions={},
    )
    db.add(flow)
    await db.commit()

    result = await get_cover_letter_by_job(job.id, db, "http://localhost:8001")
    assert result.cover_letter_id == cl.id


# ---------------------------------------------------------------------------
# Task 8 — _apply_section_overrides helper
# ---------------------------------------------------------------------------

def test_apply_section_overrides_body():
    from applire.services.cover_letter import _apply_section_overrides

    data = {"body": {"paragraphs": ["original"]}, "closing": "Regards"}
    result = _apply_section_overrides(data, {"body": "New body text."})
    assert result["body"]["paragraphs"] == ["New body text."]
    assert data["body"]["paragraphs"] == ["original"]  # original unchanged


def test_apply_section_overrides_other_dict_key():
    from applire.services.cover_letter import _apply_section_overrides

    data = {"opening": {"text": "Dear Sir"}}
    result = _apply_section_overrides(data, {"opening": "Overridden"})
    assert result["opening"]["_override"] == "Overridden"


def test_apply_section_overrides_no_overrides():
    from applire.services.cover_letter import _apply_section_overrides

    data = {"body": {"paragraphs": ["p1"]}}
    result = _apply_section_overrides(data, {})
    assert result == data


# ---------------------------------------------------------------------------
# F3 — _apply_recipient_overrides: user-typed dialog input wins over the LLM
# ---------------------------------------------------------------------------

def test_apply_recipient_overrides_fills_null_company_from_user_input():
    """F3 (blind PQ blocker) — the user typed "Helvia Pharma Services GmbH" in the
    generate dialog, but the LLM returned recipient.company = null and it was never
    overlaid, so the letter shipped with no addressee company at all."""
    from applire.services.cover_letter import _apply_recipient_overrides

    letter_data = {
        "recipient": {"name": None, "title": "Personalleiterin", "company": None, "address": None},
        "body": {"paragraphs": ["..."]},
    }
    pre_gen = {"recipient_company": "Helvia Pharma Services GmbH", "recipient_name": None}

    result = _apply_recipient_overrides(letter_data, pre_gen)
    assert result["recipient"]["company"] == "Helvia Pharma Services GmbH"


def test_apply_recipient_overrides_user_input_wins_over_llm_value():
    """User input wins over ANY LLM output, not just null — the LLM cannot be
    trusted to keep the user's typed recipient (AC #2)."""
    from applire.services.cover_letter import _apply_recipient_overrides

    letter_data = {"recipient": {"name": "Some Wrong Name", "company": "Wrong Company Inc."}}
    pre_gen = {"recipient_name": "Frau Dr. Weber", "recipient_company": "Helvia Pharma Services GmbH"}

    result = _apply_recipient_overrides(letter_data, pre_gen)
    assert result["recipient"]["name"] == "Frau Dr. Weber"
    assert result["recipient"]["company"] == "Helvia Pharma Services GmbH"


def test_apply_recipient_overrides_no_user_input_keeps_llm_value():
    """When the user left the field blank, the LLM's extracted/guessed value
    (e.g. from the JD) must be preserved, not blanked out."""
    from applire.services.cover_letter import _apply_recipient_overrides

    letter_data = {"recipient": {"name": "Herr Müller", "company": "TechVision GmbH"}}
    pre_gen = {"recipient_name": None, "recipient_company": ""}

    result = _apply_recipient_overrides(letter_data, pre_gen)
    assert result["recipient"]["name"] == "Herr Müller"
    assert result["recipient"]["company"] == "TechVision GmbH"


def test_apply_recipient_overrides_missing_recipient_key():
    """LLM output missing the recipient key entirely must not raise — the overlay
    creates it from user input."""
    from applire.services.cover_letter import _apply_recipient_overrides

    letter_data = {"body": {"paragraphs": ["..."]}}
    pre_gen = {"recipient_company": "Helvia Pharma Services GmbH"}

    result = _apply_recipient_overrides(letter_data, pre_gen)
    assert result["recipient"]["company"] == "Helvia Pharma Services GmbH"


# ---------------------------------------------------------------------------
# F3 — subject line references the target role (AC #3)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cover_letter_html_subject_includes_role_title(db):
    """AC #3 — the rendered subject must reference the target role, not just say
    the bare word "Application"/"Bewerbung"."""
    from applire.models.cover_letter import GeneratedCoverLetter, CoverLetterStatus
    from applire.models.job import JobAnalysis
    from applire.services.cover_letter import get_cover_letter_html

    job = JobAnalysis(
        raw_text_hash="hash-subject-role-1",
        raw_text="We are hiring a Head of Computerized Systems Validation (CSV/CSA).",
        role_title="Head of Computerized Systems Validation (CSV/CSA)",
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="en",
        company_name="Helvia Pharma Services GmbH",
    )
    db.add(job)
    await db.flush()

    letter_data = {
        "header": {"name": "Marcus Bauer", "address": "Musterstraße 1", "phone": "", "email": "m@test.com"},
        "recipient": {"name": None, "title": None, "company": "Helvia Pharma Services GmbH", "address": None, "date": None},
        "body": {"paragraphs": ["Dear Hiring Manager,"]},
        "signature": {"closing": "Kind regards", "name": "Marcus Bauer"},
    }
    cl = GeneratedCoverLetter(
        job_analysis_id=job.id,
        profile_id=uuid.uuid4(),
        template="classic_german",
        letter_data=letter_data,
        pre_gen_inputs={},
        status=CoverLetterStatus.ready.value,
    )
    db.add(cl)
    await db.commit()
    await db.refresh(cl)

    html = await get_cover_letter_html(cl.id, db)
    assert "Head of Computerized Systems Validation (CSV/CSA)" in html
    assert "Helvia Pharma Services GmbH" in html


@pytest.mark.asyncio
async def test_cover_letter_html_subject_no_job_falls_back_gracefully(db):
    """No JobAnalysis row resolvable (legacy/dangling FK) must not crash rendering —
    subject falls back to the bare prefix, matching pre-fix behaviour."""
    from applire.models.cover_letter import GeneratedCoverLetter, CoverLetterStatus
    from applire.services.cover_letter import get_cover_letter_html

    letter_data = {
        "header": {"name": "Marcus Bauer", "address": "Musterstraße 1", "phone": "", "email": "m@test.com"},
        "recipient": {"name": None, "title": None, "company": None, "address": None, "date": None},
        "body": {"paragraphs": ["..."]},
        "signature": {"closing": "Mit freundlichen Grüßen", "name": "Marcus Bauer"},
    }
    cl = GeneratedCoverLetter(
        job_analysis_id=uuid.uuid4(),  # dangling — no JobAnalysis row
        profile_id=uuid.uuid4(),
        template="classic_german",
        letter_data=letter_data,
        pre_gen_inputs={},
        status=CoverLetterStatus.ready.value,
    )
    db.add(cl)
    await db.commit()
    await db.refresh(cl)

    html = await get_cover_letter_html(cl.id, db)
    assert "<html" in html


# ---------------------------------------------------------------------------
# Task 9 — cover letter router (FastAPI AsyncClient, in-memory SQLite)
# ---------------------------------------------------------------------------

import os

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from applire.db.session import get_db
from applire.auth import get_auth_provider
from applire.auth.no_auth import NoAuthProvider
from applire.providers import get_provider
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def router_db():
    """Separate in-memory SQLite for router tests."""
    from applire.db.session import Base
    import applire.models.user
    import applire.models.job
    import applire.models.profile
    import applire.models.gap
    import applire.models.cv
    import applire.models.cover_letter
    import applire.models.session
    import applire.models.application
    import applire.models.flow
    import applire.models.uploads

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def cl_client(router_db):
    """FastAPI test client for cover-letter router with mocked dependencies."""
    from unittest.mock import AsyncMock, MagicMock
    from applire.routers.cover_letter import router as cl_router

    mock_provider = AsyncMock()

    _app = FastAPI()
    _app.include_router(cl_router)
    _app.dependency_overrides[get_db] = lambda: router_db
    _app.dependency_overrides[get_auth_provider] = lambda: NoAuthProvider()

    # Override the LLM provider dependency used by the router
    from applire.routers.cover_letter import _get_provider
    _app.dependency_overrides[_get_provider] = lambda: mock_provider

    transport = ASGITransport(app=_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, router_db, mock_provider


@pytest.mark.asyncio
async def test_router_post_generate_404_no_flow(cl_client):
    client, db, _ = cl_client
    payload = {"job_id": str(uuid.uuid4()), "tone": "formal"}
    resp = await client.post("/api/cover-letter/generate", json=payload)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_router_get_status_404_unknown(cl_client):
    client, db, _ = cl_client
    resp = await client.get(f"/api/cover-letter/{uuid.uuid4()}/status")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_router_get_html_404_unknown(cl_client):
    client, db, _ = cl_client
    resp = await client.get(f"/api/cover-letter/{uuid.uuid4()}/html")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_router_get_html_409_not_ready(cl_client):
    client, db, _ = cl_client
    from applire.models.cover_letter import GeneratedCoverLetter, CoverLetterStatus

    cl = GeneratedCoverLetter(
        job_analysis_id=uuid.uuid4(),
        profile_id=uuid.uuid4(),
        template="classic_german",
        letter_data={},
        pre_gen_inputs={},
        status=CoverLetterStatus.pending.value,
    )
    db.add(cl)
    await db.commit()
    await db.refresh(cl)

    resp = await client.get(f"/api/cover-letter/{cl.id}/html")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_router_patch_section_404_unknown(cl_client):
    client, db, _ = cl_client
    payload = {"section": "body", "content": "Hello world"}
    resp = await client.patch(f"/api/cover-letter/{uuid.uuid4()}/section", json=payload)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_router_patch_section_ok(cl_client):
    from unittest.mock import AsyncMock, patch as mock_patch
    client, db, _ = cl_client
    from applire.models.cover_letter import GeneratedCoverLetter, CoverLetterStatus

    cl = GeneratedCoverLetter(
        job_analysis_id=uuid.uuid4(),
        profile_id=uuid.uuid4(),
        template="classic_german",
        letter_data={},
        pre_gen_inputs={},
        status=CoverLetterStatus.ready.value,
    )
    db.add(cl)
    await db.commit()
    await db.refresh(cl)

    # Patch the background ATS re-audit to avoid PostgreSQL connection in unit tests
    with mock_patch(
        "applire.services.cover_letter._update_ats_report_letter_by_id",
        new=AsyncMock(),
    ):
        payload = {"section": "body", "content": "Updated text"}
        resp = await client.patch(f"/api/cover-letter/{cl.id}/section", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["cover_letter_id"] == str(cl.id)
    assert data["section"] == "body"


@pytest.mark.asyncio
async def test_router_get_by_job_404_no_flow(cl_client):
    client, db, _ = cl_client
    resp = await client.get(f"/api/cover-letter/by-job/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_router_post_generate_creates_pending_record(cl_client):
    """POST /generate with a seeded flow session creates a pending CL record."""
    from unittest.mock import patch, AsyncMock as AM
    client, db, mock_provider = cl_client
    from applire.models.cover_letter import GeneratedCoverLetter, CoverLetterStatus
    from applire.models.cv import GeneratedCV
    from applire.models.flow import FlowSession
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.user import User

    user = User(id=uuid.uuid4(), email="router@test.com")
    db.add(user)
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="router123",
        raw_text="Backend Engineer at Acme",
        role_title="Backend Engineer",
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="mid",
        company_culture_signals=[],
        language_requirement="de",
    )
    db.add(job)
    profile = MasterProfile(profile_json={
        "contact": {"name": "Test User", "email": "t@t.com"},
        "summary": "Dev",
        "work_history": [],
        "skills": [],
        "education": [],
        "languages": [],
    })
    db.add(profile)
    await db.flush()

    cv = GeneratedCV(
        job_analysis_id=job.id,
        profile_id=profile.id,
        tailored_data={},
        template="executive",
        status="ready",
    )
    db.add(cv)
    await db.flush()

    flow = FlowSession(
        user_id=user.id,
        job_id=job.id,
        generated_cv_id=cv.id,
        available_actions={},
    )
    db.add(flow)
    await db.commit()

    # Patch background task so it doesn't attempt a real DB connection
    with patch(
        "applire.services.cover_letter._render_cover_letter_background",
        new=AM(return_value=None),
    ):
        payload = {"job_id": str(job.id), "tone": "formal"}
        resp = await client.post("/api/cover-letter/generate", json=payload)

    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == CoverLetterStatus.pending.value
    assert "cover_letter_id" in data


@pytest.mark.asyncio
async def test_router_get_cl_status_ok(cl_client):
    client, db, _ = cl_client
    from applire.models.cover_letter import GeneratedCoverLetter, CoverLetterStatus

    cl = GeneratedCoverLetter(
        job_analysis_id=uuid.uuid4(),
        profile_id=uuid.uuid4(),
        template="classic_german",
        letter_data={},
        pre_gen_inputs={},
        status=CoverLetterStatus.generating.value,
    )
    db.add(cl)
    await db.commit()
    await db.refresh(cl)

    resp = await client.get(f"/api/cover-letter/{cl.id}/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == CoverLetterStatus.generating.value


@pytest.mark.asyncio
async def test_router_get_pdf_404_unknown(cl_client):
    """PDF endpoint returns 404 when cover letter does not exist (render_pdf raises LookupError)."""
    from unittest.mock import patch
    client, db, _ = cl_client
    with patch(
        "applire.services.cover_letter_pdf.render_pdf",
        side_effect=LookupError("Cover letter not found"),
    ):
        resp = await client.get(f"/api/cover-letter/{uuid.uuid4()}/pdf")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_router_get_pdf_409_not_ready(cl_client):
    """PDF endpoint returns 409 when cover letter is not in ready state."""
    from unittest.mock import patch
    client, db, _ = cl_client

    with patch(
        "applire.services.cover_letter_pdf.render_pdf",
        side_effect=ValueError("Cover letter not ready"),
    ):
        resp = await client.get(f"/api/cover-letter/{uuid.uuid4()}/pdf")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_router_get_by_job_ok(cl_client):
    """GET /by-job/{job_id} returns status response for existing flow + CL."""
    client, db, _ = cl_client
    from applire.models.cover_letter import GeneratedCoverLetter, CoverLetterStatus
    from applire.models.flow import FlowSession
    from applire.models.job import JobAnalysis
    from applire.models.user import User

    user = User(id=uuid.uuid4(), email="byjrouter@test.com")
    db.add(user)
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="byjr001",
        raw_text="Dev role",
        role_title="Dev",
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="mid",
        company_culture_signals=[],
        language_requirement="de",
    )
    db.add(job)
    cl = GeneratedCoverLetter(
        job_analysis_id=job.id,
        profile_id=uuid.uuid4(),
        template="executive",
        letter_data={},
        pre_gen_inputs={},
        status=CoverLetterStatus.ready.value,
    )
    db.add(cl)
    await db.flush()

    flow = FlowSession(
        user_id=user.id,
        job_id=job.id,
        generated_cover_letter_id=cl.id,
        available_actions={},
    )
    db.add(flow)
    await db.commit()

    resp = await client.get(f"/api/cover-letter/by-job/{job.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cover_letter_id"] == str(cl.id)


# ---------------------------------------------------------------------------
# Task 8 — get_cover_letter_html service (Jinja2 rendering)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_cover_letter_html_renders_template(db):
    """get_cover_letter_html renders valid HTML for a ready cover letter."""
    from applire.models.cover_letter import GeneratedCoverLetter, CoverLetterStatus
    from applire.services.cover_letter import get_cover_letter_html

    letter_data = {
        "header": {"name": "Marcus Bauer", "address": "Musterstraße 1", "phone": "", "email": "m@test.com"},
        "recipient": {"name": "Dr. Müller", "title": "", "company": "Roche", "address": "", "date": "14. April 2026"},
        "subject": "Bewerbung als QA Manager",
        "opening": "Sehr geehrte Frau Dr. Müller,",
        "body": {"paragraphs": ["Ich bewerbe mich auf die ausgeschriebene Stelle."]},
        "signature": {"closing": "Mit freundlichen Grüßen", "name": "Marcus Bauer"},
    }

    cl = GeneratedCoverLetter(
        job_analysis_id=uuid.uuid4(),
        profile_id=uuid.uuid4(),
        template="classic_german",
        letter_data=letter_data,
        pre_gen_inputs={},
        status=CoverLetterStatus.ready.value,
    )
    db.add(cl)
    await db.commit()
    await db.refresh(cl)

    html = await get_cover_letter_html(cl.id, db)
    assert "<html" in html
    assert "Marcus Bauer" in html


@pytest.mark.asyncio
async def test_get_cover_letter_html_not_found(db):
    from applire.services.cover_letter import get_cover_letter_html
    with pytest.raises(LookupError):
        await get_cover_letter_html(uuid.uuid4(), db)


@pytest.mark.asyncio
async def test_get_cover_letter_html_not_ready(db):
    from applire.models.cover_letter import GeneratedCoverLetter, CoverLetterStatus
    from applire.services.cover_letter import get_cover_letter_html

    cl = GeneratedCoverLetter(
        job_analysis_id=uuid.uuid4(),
        profile_id=uuid.uuid4(),
        template="classic_german",
        letter_data={},
        pre_gen_inputs={},
        status=CoverLetterStatus.pending.value,
    )
    db.add(cl)
    await db.commit()
    await db.refresh(cl)

    with pytest.raises(ValueError):
        await get_cover_letter_html(cl.id, db)


@pytest.mark.asyncio
async def test_get_cover_letter_status_ready_includes_letter_data(db):
    """When status is ready, letter_data must be returned in the response."""
    from applire.models.cover_letter import GeneratedCoverLetter, CoverLetterStatus
    from applire.services.cover_letter import get_cover_letter_status

    letter_data = {"header": {"name": "Test User"}, "body": {"paragraphs": ["Hello"]}}
    cl = GeneratedCoverLetter(
        job_analysis_id=uuid.uuid4(),
        profile_id=uuid.uuid4(),
        template="classic_german",
        letter_data=letter_data,
        pre_gen_inputs={},
        status=CoverLetterStatus.ready.value,
    )
    db.add(cl)
    await db.commit()
    await db.refresh(cl)

    result = await get_cover_letter_status(cl.id, db, "http://localhost:8001")
    assert result.letter_data == letter_data


@pytest.mark.asyncio
async def test_get_cover_letter_status_pending_letter_data_is_none(db):
    """When status is not ready, letter_data must be None."""
    from applire.models.cover_letter import GeneratedCoverLetter, CoverLetterStatus
    from applire.services.cover_letter import get_cover_letter_status

    cl = GeneratedCoverLetter(
        job_analysis_id=uuid.uuid4(),
        profile_id=uuid.uuid4(),
        template="classic_german",
        letter_data={"body": {"paragraphs": ["draft"]}},
        pre_gen_inputs={},
        status=CoverLetterStatus.generating.value,
    )
    db.add(cl)
    await db.commit()
    await db.refresh(cl)

    result = await get_cover_letter_status(cl.id, db, "http://localhost:8001")
    assert result.letter_data is None


# ---------------------------------------------------------------------------
# E039/US219 — cover-letter download filename (FMEA JF-E-Q.1)
# Mirrors the CV filename contract; the "_Anschreiben" suffix keeps the pair
# from colliding in a Downloads folder when both artifacts are fetched.
# ---------------------------------------------------------------------------


async def _seed_letter_with_context(
    db,
    *,
    profile_name: str | None = "Emma Weber",
    company_name: str | None = "DataCraft GmbH",
    role_title: str = "Data Analyst",
    jd_language: str | None = None,
):
    """MasterProfile + JobAnalysis + ready cover letter. Returns cl_id.

    ``jd_language`` (issue #241 item 3) — the letter's actual output language is
    resolved from the JD's language (ADR-038), not a language field on the
    letter itself. None leaves ``jd_language`` unset, falling back to
    detection on ``raw_text`` ("Sample JD" ties → 'de', the DACH-first default).
    """
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile

    profile = MasterProfile(
        profile_json={"personal_info": {"name": profile_name}} if profile_name else {},
    )
    job = JobAnalysis(
        raw_text_hash=f"hash-{uuid.uuid4()}",
        raw_text="Sample JD",
        role_title=role_title,
        company_name=company_name,
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="Senior",
        company_culture_signals=[],
        language_requirement="German",
        jd_language=jd_language,
    )
    db.add_all([profile, job])
    await db.flush()

    cl = GeneratedCoverLetter(
        job_analysis_id=job.id,
        profile_id=profile.id,
        template="classic_german",
        letter_data={},
        pre_gen_inputs={},
        status="ready",
    )
    db.add(cl)
    await db.commit()
    await db.refresh(cl)
    return cl.id


@pytest.mark.asyncio
async def test_cover_letter_pdf_filename_is_name_company_role(db):
    from applire.services.cover_letter import get_cover_letter_pdf_filename

    cl_id = await _seed_letter_with_context(db)
    filename = await get_cover_letter_pdf_filename(cl_id, db)
    assert filename == "Emma-Weber_DataCraft-GmbH_Data-Analyst_Anschreiben.pdf"


@pytest.mark.asyncio
async def test_cover_letter_pdf_filename_transliterates_umlauts(db):
    from applire.services.cover_letter import get_cover_letter_pdf_filename

    cl_id = await _seed_letter_with_context(
        db, profile_name="Jörg Groß", company_name="Über GmbH", role_title="Bäcker"
    )
    filename = await get_cover_letter_pdf_filename(cl_id, db)
    assert filename == "Joerg-Gross_Ueber-GmbH_Baecker_Anschreiben.pdf"


@pytest.mark.asyncio
async def test_cover_letter_pdf_filename_falls_back_when_parts_missing(db):
    from applire.services.cover_letter import get_cover_letter_pdf_filename

    cl_id = await _seed_letter_with_context(
        db, profile_name=None, company_name=None, role_title=""
    )
    filename = await get_cover_letter_pdf_filename(cl_id, db)
    assert filename == f"anschreiben-{str(cl_id)[:8]}.pdf"


@pytest.mark.asyncio
async def test_cover_letter_pdf_filename_uses_english_suffix_for_english_jd(db):
    """issue #241 item 3 — an English-JD letter must not carry a German
    filename suffix. Language follows resolve_jd_language (ADR-038), matching
    the letter's actual output language, not a hardcoded default."""
    from applire.services.cover_letter import get_cover_letter_pdf_filename

    cl_id = await _seed_letter_with_context(db, jd_language="en")
    filename = await get_cover_letter_pdf_filename(cl_id, db)
    assert filename == "Emma-Weber_DataCraft-GmbH_Data-Analyst_Cover-Letter.pdf"


@pytest.mark.asyncio
async def test_cover_letter_pdf_filename_english_fallback_when_parts_missing(db):
    from applire.services.cover_letter import get_cover_letter_pdf_filename

    cl_id = await _seed_letter_with_context(
        db, profile_name=None, company_name=None, role_title="", jd_language="en",
    )
    filename = await get_cover_letter_pdf_filename(cl_id, db)
    assert filename == f"cover-letter-{str(cl_id)[:8]}.pdf"


@pytest.mark.asyncio
async def test_cover_letter_pdf_filename_still_german_when_jd_language_explicitly_de(db):
    """Explicit 'de' stays on the existing German suffix — no behaviour change
    for the common case, only the previously-hardcoded English case is fixed."""
    from applire.services.cover_letter import get_cover_letter_pdf_filename

    cl_id = await _seed_letter_with_context(db, jd_language="de")
    filename = await get_cover_letter_pdf_filename(cl_id, db)
    assert filename == "Emma-Weber_DataCraft-GmbH_Data-Analyst_Anschreiben.pdf"


# ---------------------------------------------------------------------------
# #189 — sign-off language + sender-name backfill (ADR-038 chrome consistency)
#
# An English letter closed with the German "Mit freundlichen Grüßen," (the LLM
# was primed to German by the schema example + the mock hardcodes it) and had NO
# sender name after it (signature.name was LLM-owned and came out empty on the
# fallback profile_json path). Both are now fixed deterministically, ADR-038
# aligned: the sign-off is chrome routed by output language, and the sender name
# is backfilled from the candidate's real name across BOTH profile schemas.
# ---------------------------------------------------------------------------

import copy as _copy


def test_cover_letter_labels_carry_language_routed_closing():
    """(A) — the sign-off is chrome, so it lives in the label map keyed by output
    language, not in the LLM's JSON (which bypassed ADR-038 label routing)."""
    from applire.templates.labels import cover_letter_labels

    assert cover_letter_labels("en")["closing"] == "Kind regards"
    assert cover_letter_labels("de")["closing"] == "Mit freundlichen Grüßen"


def test_normalize_signature_closing_overwrites_german_priming_for_en():
    """(A) — an EN letter must close with the English chrome label, deterministically,
    overwriting whatever the LLM (or the German-primed mock) emitted."""
    from applire.services.cover_letter import _normalize_signature_closing

    letter_data = {"signature": {"closing": "Mit freundlichen Grüßen", "name": "Anna Bauer"}}
    result = _normalize_signature_closing(letter_data, "en")
    assert result["signature"]["closing"] == "Kind regards"


def test_normalize_signature_closing_keeps_german_for_de():
    """(A) — a DE letter's sign-off is the German chrome label, even if the LLM drifted
    to English."""
    from applire.services.cover_letter import _normalize_signature_closing

    letter_data = {"signature": {"closing": "Kind regards", "name": "Anna Bauer"}}
    result = _normalize_signature_closing(letter_data, "de")
    assert result["signature"]["closing"] == "Mit freundlichen Grüßen"


def test_normalize_signature_closing_creates_signature_when_missing():
    """(A) — a missing signature key must not crash; the routed closing is created."""
    from applire.services.cover_letter import _normalize_signature_closing

    result = _normalize_signature_closing({}, "en")
    assert result["signature"]["closing"] == "Kind regards"


def test_normalize_signature_closing_on_mock_llm_output_en():
    """(A) — the mock provider hardcodes the German closing (providers/llm/mock.py),
    faithfully reproducing the bug for an EN letter. Running the mock's own
    cover-letter response through the EN normalize step must yield the English label,
    hermetically."""
    from applire.providers.llm.mock import _COVER_LETTER_RESPONSE
    from applire.services.cover_letter import _normalize_signature_closing

    letter_data = _copy.deepcopy(_COVER_LETTER_RESPONSE)
    assert letter_data["signature"]["closing"] == "Mit freundlichen Grüßen"  # bug reproduced
    result = _normalize_signature_closing(letter_data, "en")
    assert result["signature"]["closing"] == "Kind regards"
    assert result["signature"]["name"]  # sender name present


def test_backfill_sender_name_from_personal_info_fallback_schema():
    """(B) — when signature.name/header.name come out empty (the fallback cv_data path
    is profile.profile_json, whose schema uses 'personal_info', not 'contact', so the
    prompt fed the LLM a blank name), the sender name is deterministically backfilled
    from the profile's personal_info block."""
    from applire.services.cover_letter import _backfill_sender_name

    class _P:
        profile_json = {"personal_info": {"name": "Priya Sharma"}}

    letter_data = {"header": {"name": ""}, "signature": {"closing": "Kind regards", "name": ""}}
    result = _backfill_sender_name(letter_data, cv_data={}, profile=_P())
    assert result["signature"]["name"] == "Priya Sharma"
    assert result["header"]["name"] == "Priya Sharma"


def test_backfill_sender_name_from_contact_schema():
    """(B) — the tailored-CV path uses the 'contact' schema; the name is sourced there."""
    from applire.services.cover_letter import _backfill_sender_name

    letter_data = {"signature": {"name": ""}}
    result = _backfill_sender_name(
        letter_data, cv_data={"contact": {"name": "Marcus Bauer"}}, profile=None
    )
    assert result["signature"]["name"] == "Marcus Bauer"


def test_backfill_sender_name_preserves_existing_llm_name():
    """(B) — backfill only fills a MISSING name; a name the LLM already produced wins."""
    from applire.services.cover_letter import _backfill_sender_name

    letter_data = {"signature": {"name": "Real Name"}}
    result = _backfill_sender_name(
        letter_data, cv_data={"contact": {"name": "Other"}}, profile=None
    )
    assert result["signature"]["name"] == "Real Name"


def test_build_cover_letter_prompt_reads_personal_info_name_fallback():
    """(B) upstream — the fallback cv_data path is profile.profile_json ('personal_info'
    schema). build_cover_letter_prompt must read the name from either schema so it stops
    feeding the LLM a blank name (mirrors services/cv.py:_contact_from_profile)."""
    from applire.prompts.cover_letter import build_cover_letter_prompt

    prompt = build_cover_letter_prompt(
        cv_data={"personal_info": {"name": "Priya Sharma"}, "summary": "Engineer"},
        jd_text="We are hiring.",
        pre_gen_inputs={"tone": "formal"},
        detected_language="en",
    )
    assert "Priya Sharma" in prompt


def test_cover_letter_schema_example_not_primed_to_german():
    """(A) — the SYSTEM_PROMPT schema example previously hardcoded 'Mit freundlichen
    Grüßen', priming EVERY letter (incl. EN) to German. The example must be
    language-neutral so the model is not mis-primed."""
    from applire.prompts.cover_letter import SYSTEM_PROMPT

    assert "Mit freundlichen Grüßen" not in SYSTEM_PROMPT


def test_letter_html_render_en_signoff_and_backfilled_name():
    """(A)+(B) chrome render (hermetic, no PDF) — after the deterministic post-steps an
    EN letter renders the English closing and a non-empty sender-name div, even when the
    LLM emitted the German closing and a blank name."""
    from applire.services.cover_letter import (
        _jinja_env,
        _backfill_sender_name,
        _default_color_context,
        _normalize_signature_closing,
        _TEMPLATE_FILES,
    )
    from applire.templates.labels import cover_letter_labels

    class _P:
        profile_json = {"personal_info": {"name": "Catherine O'Brien"}}

    letter_data = {
        "header": {"name": "", "address": "Bahnhofstrasse 21, 8001 Zürich", "phone": None, "email": "c@example.com"},
        "recipient": {"name": "Mr. Weber", "title": None, "company": "Müller & Söhne AG", "address": None, "date": "11 June 2026"},
        "body": {"paragraphs": ["I am writing to express my strong interest."]},
        "signature": {"closing": "Mit freundlichen Grüßen", "name": ""},
    }
    letter_data = _normalize_signature_closing(letter_data, "en")
    letter_data = _backfill_sender_name(letter_data, cv_data={}, profile=_P())

    html = _jinja_env.get_template(_TEMPLATE_FILES["classic_german"]).render(
        letter=letter_data,
        color=_default_color_context(),
        lang="en",
        labels=cover_letter_labels("en"),
        subject="Application: Lead Platform Engineer",
    )
    assert "Kind regards" in html
    assert "Mit freundlichen Grüßen" not in html
    # sender-name div must be non-empty (the bug rendered an empty <div>)
    assert "Catherine O&#39;Brien" in html or "Catherine O'Brien" in html
