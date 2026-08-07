# Copyright (C) 2024-2026 Tobias Rosenbaum
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

"""#271 — the JD's leadership-vs-hands-on weighting is DATA, not a marker check.

Pinned case: charter run #5 — the posting states "~60% technical leadership /
40% hands-on" and the delivered letter is 100% technical narrative. Before this
fix that sentence had no representation anywhere in ``JobAnalysis``; selection
reduced the whole question to ``jd_signals_leadership()``, a 15-word substring
list that answers "does the word 'leadership' occur" and can never answer
"how much". The leadership sub-cap was a flat 4 whether the posting said 10%
or 90%.

Shape follows the ADR-069 ``scope_requirements`` precedent exactly: a
model-extracted facet whose verbatim JD ``quote`` is its identity, never
invented when the posting is silent, floored deterministically in
``services/job.py``, and consumed downstream as data.

No test here asserts model BEHAVIOUR. They pin the schema (can the model emit
it), the prompt contract (is it asked for), the deterministic floor, and the
wiring (does the stored facet reach and change the selector).
"""

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.prompts.job_analysis import SYSTEM_PROMPT as JD_SYSTEM_PROMPT
from applire.prompts.review_job_analysis import JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT
from applire.schemas.job import JobAnalysisResponse
from applire.services.job import _coerce_leadership_emphasis
from applire.services.vault_evidence import select_vault_evidence

# The run-5 posting sentence, verbatim.
_JD_TEXT = (
    "Your role\n"
    "The split is roughly 60% technical leadership / 40% hands-on work.\n"
    "You will grow a distributed group of engineers.\n"
)
_QUOTE = "The split is roughly 60% technical leadership / 40% hands-on work."

_FACET = {"emphasis": "leadership_led", "quote": _QUOTE}


# ── Step 2 of the prompt-first chain: CAN the model emit it? ────────────────


class TestPromptSchemaAsksForTheFacet:
    def test_the_extraction_schema_declares_the_field(self):
        """Category A by construction until this passes: a field absent from the
        prompt's own JSON schema cannot be emitted, whatever the rules say."""
        assert '"leadership_emphasis"' in JD_SYSTEM_PROMPT

    def test_the_schema_declares_the_closed_emphasis_vocabulary(self):
        for value in ("leadership_led", "balanced", "hands_on_led"):
            assert value in JD_SYSTEM_PROMPT, value

    def test_the_prompt_states_the_no_invention_rule(self):
        """ADR-069 clause 1's own discipline: silent posting ⇒ null, never a guess."""
        assert "LEADERSHIP EMPHASIS" in JD_SYSTEM_PROMPT
        assert "null" in JD_SYSTEM_PROMPT.split("LEADERSHIP EMPHASIS")[1]

    def test_the_quote_is_declared_as_the_facet_identity(self):
        section = JD_SYSTEM_PROMPT.split("LEADERSHIP EMPHASIS")[1]
        assert "verbatim" in section.lower()

    def test_the_reviewer_grounds_the_quote_against_the_posting(self):
        """Same control ADR-069 clause 1 gave the scope quote — an invented
        weighting is bounded by a quote the reviewer can check."""
        assert "leadership_emphasis" in JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT


# ── The deterministic floor (facts only, ADR-062 clause 1) ──────────────────


class TestCoerceLeadershipEmphasis:
    def test_a_well_formed_facet_survives(self):
        assert _coerce_leadership_emphasis(_FACET, _JD_TEXT) == {
            "emphasis": "leadership_led",
            "quote": _QUOTE,
        }

    def test_a_quote_absent_from_the_posting_is_dropped(self):
        fabricated = {"emphasis": "leadership_led", "quote": "90% people management."}
        assert _coerce_leadership_emphasis(fabricated, _JD_TEXT) is None

    def test_the_quote_match_folds_whitespace(self):
        wrapped = {"emphasis": "balanced", "quote": _QUOTE.replace(" / ", "\n/ ")}
        assert _coerce_leadership_emphasis(wrapped, _JD_TEXT) is not None

    def test_an_emphasis_outside_the_closed_set_is_dropped(self):
        assert _coerce_leadership_emphasis(
            {"emphasis": "mostly_leadership", "quote": _QUOTE}, _JD_TEXT
        ) is None

    def test_a_missing_or_empty_quote_is_dropped(self):
        assert _coerce_leadership_emphasis({"emphasis": "balanced"}, _JD_TEXT) is None
        assert _coerce_leadership_emphasis(
            {"emphasis": "balanced", "quote": "   "}, _JD_TEXT
        ) is None

    def test_non_dict_and_absent_payloads_are_none(self):
        assert _coerce_leadership_emphasis(None, _JD_TEXT) is None
        assert _coerce_leadership_emphasis("leadership_led", _JD_TEXT) is None
        assert _coerce_leadership_emphasis([], _JD_TEXT) is None

    def test_unknown_extra_keys_are_not_persisted(self):
        """The stored facet is exactly the two consumed fields — nothing the
        model volunteers rides along into the ORM."""
        noisy = dict(_FACET, leadership_pct=60, confidence="high")
        assert _coerce_leadership_emphasis(noisy, _JD_TEXT) == {
            "emphasis": "leadership_led",
            "quote": _QUOTE,
        }


class TestResponseSchema:
    def test_a_legacy_row_reads_as_none(self):
        record = MagicMock()
        record.id = uuid.uuid4()
        record.role_title = "Engineer"
        record.required_skills = []
        record.nice_to_have_skills = []
        record.keywords = []
        record.seniority_level = "Senior"
        record.company_culture_signals = []
        record.language_requirement = "English"
        record.company_name = None
        record.berufsbild_code = None
        record.berufsbild_label = None
        record.raw_text_hash = "h" * 64
        record.source_url = None
        record.scope_requirements = None
        record.leadership_emphasis = None
        record.duplicate_of = None
        assert JobAnalysisResponse.model_validate(record).leadership_emphasis is None

    def test_a_stored_facet_round_trips(self):
        record = MagicMock()
        record.id = uuid.uuid4()
        record.role_title = "Engineer"
        record.required_skills = []
        record.nice_to_have_skills = []
        record.keywords = []
        record.seniority_level = "Senior"
        record.company_culture_signals = []
        record.language_requirement = "English"
        record.company_name = None
        record.berufsbild_code = None
        record.berufsbild_label = None
        record.raw_text_hash = "h" * 64
        record.source_url = None
        record.scope_requirements = None
        record.leadership_emphasis = dict(_FACET)
        record.duplicate_of = None
        parsed = JobAnalysisResponse.model_validate(record)
        assert parsed.leadership_emphasis is not None
        assert parsed.leadership_emphasis.emphasis == "leadership_led"
        assert parsed.leadership_emphasis.quote == _QUOTE


# ── Selection: the facet is the trigger, and it sets the sub-cap ────────────


def _leadership_vault(n: int) -> dict:
    """A vault entry carrying ``n`` distinct leadership sentences plus one
    anchor for the ledger concept, so channel 1 always fires too."""
    return {
        "work_experience": [
            {
                "id": "w1",
                "company": "NordPharm",
                "role": "Staff Engineer",
                "start_date": "2015-01",
                "end_date": None,
                "is_current": True,
                "responsibilities": [
                    "Built the retrieval pipeline end to end.",
                ]
                + [
                    f"Mentoring engineer number {i} through their promotion cycle."
                    for i in range(n)
                ],
                "achievements": [],
            }
        ],
        "projects": [],
        "skills": [],
        "education": [],
        "languages": [],
        "personal_info": {"name": "Kaile", "email": None},
    }


_LEDGER = [
    {
        "concept": "retrieval pipeline",
        "surface_forms": ["retrieval pipeline"],
        "claimable": True,
        "status": "direct",
        "sources": ["required"],
        "fit_weight": 1.0,
        "evidence": "listed",
    }
]


def _leadership_items(items):
    return [i for i in items if i.reason == "leadership-eligible"]


class TestSelectionReadsTheFacet:
    # The excerpt deliberately carries NO marker word from _LEADERSHIP_MARKERS,
    # so every assertion below is about the facet and nothing else.
    _NO_MARKER_EXCERPT = "We build retrieval systems. The split is roughly 60/40."

    def test_the_facet_triggers_the_leadership_channel_without_any_marker_word(self):
        items = select_vault_evidence(
            _LEDGER,
            self._NO_MARKER_EXCERPT,
            _leadership_vault(3),
            leadership_emphasis=_FACET,
        )
        assert len(_leadership_items(items)) == 3

    def test_without_the_facet_a_marker_free_excerpt_selects_no_leadership(self):
        items = select_vault_evidence(
            _LEDGER, self._NO_MARKER_EXCERPT, _leadership_vault(3)
        )
        assert _leadership_items(items) == []

    def test_a_leadership_led_posting_admits_more_evidence_than_a_balanced_one(self):
        vault = _leadership_vault(8)
        led = select_vault_evidence(
            _LEDGER, self._NO_MARKER_EXCERPT, vault,
            leadership_emphasis={"emphasis": "leadership_led", "quote": _QUOTE},
        )
        balanced = select_vault_evidence(
            _LEDGER, self._NO_MARKER_EXCERPT, vault,
            leadership_emphasis={"emphasis": "balanced", "quote": _QUOTE},
        )
        assert len(_leadership_items(led)) > len(_leadership_items(balanced))

    def test_a_hands_on_led_posting_admits_less_evidence_than_a_balanced_one(self):
        """The issue's own complaint, inverted: a flat cap of 4 was applied
        whether the posting said 10% leadership or 90%."""
        vault = _leadership_vault(8)
        hands_on = select_vault_evidence(
            _LEDGER, self._NO_MARKER_EXCERPT, vault,
            leadership_emphasis={"emphasis": "hands_on_led", "quote": _QUOTE},
        )
        balanced = select_vault_evidence(
            _LEDGER, self._NO_MARKER_EXCERPT, vault,
            leadership_emphasis={"emphasis": "balanced", "quote": _QUOTE},
        )
        assert 0 < len(_leadership_items(hands_on)) < len(_leadership_items(balanced))

    def test_the_selected_item_carries_the_postings_own_sentence(self):
        """The facet threads into the writer prompts as INPUT: the digest item's
        concept states what the posting itself said about the balance, so the
        writer is positioning against a quote rather than a boolean."""
        items = select_vault_evidence(
            _LEDGER, self._NO_MARKER_EXCERPT, _leadership_vault(2),
            leadership_emphasis=_FACET,
        )
        concepts = {i.concept for i in _leadership_items(items)}
        assert concepts, "fixture must select leadership evidence"
        assert all(_QUOTE in c for c in concepts)

    def test_a_malformed_facet_falls_back_to_the_legacy_marker_path(self):
        """Never a crash on a row the floor did not produce (hand-written
        fixtures, older callers): an unknown emphasis is treated as absent."""
        items = select_vault_evidence(
            _LEDGER,
            "We need a mentoring culture.",
            _leadership_vault(6),
            leadership_emphasis={"emphasis": "???", "quote": _QUOTE},
        )
        assert len(_leadership_items(items)) == 4


class TestLegacyRowsAreUnchanged:
    def test_a_marker_bearing_excerpt_still_fires_with_no_facet(self):
        """Pre-migration ``job_analyses`` rows hold NULL; resolving the trigger
        at use time keeps them working, the ``jd_language`` precedent."""
        items = select_vault_evidence(
            _LEDGER,
            "You will be mentoring a team of engineers.",
            _leadership_vault(6),
        )
        assert len(_leadership_items(items)) == 4

    def test_the_legacy_concept_label_names_no_quote(self):
        items = select_vault_evidence(
            _LEDGER, "You will be mentoring a team.", _leadership_vault(2)
        )
        assert all("posting states" not in i.concept for i in _leadership_items(items))


# ── Wiring: the stored facet reaches BOTH writers ──────────────────────────


def _job_mock(facet, raw_text=_JD_TEXT):
    job = MagicMock()
    job.role_title = "Staff AI Engineer"
    job.required_skills = ["retrieval pipeline"]
    job.nice_to_have_skills = []
    job.keywords = []
    job.seniority_level = ""
    job.company_culture_signals = []
    job.language_requirement = ""
    job.raw_text = raw_text
    job.jd_language = "en"
    job.company_name = "Acme"
    job.scope_requirements = []
    job.leadership_emphasis = facet
    return job


def _tailored_raw() -> dict:
    return {
        "contact": {"name": "Kaile", "email": None, "phone": None,
                    "location": None, "linkedin": None},
        "summary": "Engineer.",
        "work_history": [{"company": "NordPharm", "role": "Staff Engineer",
                          "start_date": "2015-01", "end_date": None, "bullets": []}],
        "skills": [], "education": [], "languages": [],
    }


async def _run_cv(facet, raw_text=_JD_TEXT) -> dict:
    cv_id, job_id, profile_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    mock_cv = MagicMock()
    mock_cv.status = "pending"
    mock_cv.target_pages = 2
    mock_profile = MagicMock()
    mock_profile.profile_json = _leadership_vault(2)
    mock_gap = MagicMock()
    mock_gap.keyword_gaps = []
    mock_gap.critical_gaps = []
    mock_gap.keyword_ledger = _LEDGER
    mock_db = AsyncMock()
    mock_db.get.side_effect = lambda model, id_: {
        cv_id: mock_cv, job_id: _job_mock(facet, raw_text), profile_id: mock_profile,
    }[id_]
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_gap
    mock_db.execute.return_value = mock_result

    captured: dict = {}

    async def fake_fallback(*args, **kwargs):
        captured.update(kwargs)
        return _tailored_raw()

    with patch("applire.services.cv.AsyncSessionLocal") as mock_session_local, \
         patch("applire.services.cv.get_provider", return_value=AsyncMock()), \
         patch("applire.services.cv._tailor_cv_with_fallback", side_effect=fake_fallback), \
         patch("applire.services.cv.review_and_refine",
               new=AsyncMock(side_effect=lambda **kw: kw["draft"])), \
         patch("applire.services.cv._review_cv_language",
               new=AsyncMock(side_effect=lambda draft, *a, **kw: draft)), \
         patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"pdf")), \
         patch("applire.services.cv_section_editor.build_content_snapshot", return_value={}):
        mock_session_local.return_value.__aenter__.return_value = mock_db
        from applire.services.cv import _render_cv_background
        await _render_cv_background(cv_id, job_id, profile_id, "classic_german")
    return captured


@pytest.mark.asyncio
async def test_cv_writer_receives_the_postings_weighting_quote():
    """The JD text carries no ``_LEADERSHIP_MARKERS`` word other than the one in
    the quote itself — mutate the ``job.leadership_emphasis`` argument away at
    the call site and this fails."""
    block = (await _run_cv(_FACET)).get("vault_evidence_block") or ""
    assert _QUOTE in block


@pytest.mark.asyncio
async def test_cv_writer_gets_no_leadership_evidence_when_the_posting_is_silent():
    """No facet AND no marker word in the posting: channel 3 must stay shut, so
    the fix cannot become "every JD wants leadership evidence"."""
    block = (await _run_cv(
        None, raw_text="We build retrieval systems for regulated customers.\n"
    )).get("vault_evidence_block") or ""
    assert "Mentoring engineer" not in block


# ── Wiring: the letter chain (the issue's own consumer) ────────────────────


@pytest_asyncio.fixture
async def db():
    """In-memory SQLite session with all models registered."""
    from applire.db.session import Base  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.cover_letter  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _letter_selector_kwargs(db, facet) -> dict:
    """Drive ``_render_cover_letter_background`` far enough to capture the
    arguments the letter chain hands the vault-evidence selector."""
    from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile

    job_id = uuid.UUID("00000000-0000-0000-0000-000000000212")
    profile_id = uuid.UUID("00000000-0000-0000-0000-000000000213")
    cl_id = uuid.UUID("00000000-0000-0000-0000-000000000215")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    db.add_all([
        JobAnalysis(
            id=job_id,
            raw_text_hash="lead_emph_1",
            raw_text=_JD_TEXT,
            role_title="Staff AI Engineer",
            required_skills=["retrieval pipeline"],
            nice_to_have_skills=[],
            keywords=[],
            seniority_level="senior",
            company_culture_signals=[],
            language_requirement="en",
            leadership_emphasis=facet,
        ),
        MasterProfile(
            id=profile_id, profile_json=_leadership_vault(2),
            created_at=now, updated_at=now,
        ),
        GeneratedCoverLetter(
            id=cl_id, job_analysis_id=job_id, profile_id=profile_id,
            template="classic_german", letter_data=None,
            pre_gen_inputs={"tone": "formal"},
            status=CoverLetterStatus.pending.value,
            created_at=now, expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        ),
    ])
    await db.commit()

    captured: dict = {}
    real = None

    def spy(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return real(*args, **kwargs)

    import applire.services.vault_evidence as ve
    real = ve.select_vault_evidence

    provider = AsyncMock()
    provider.aparse_json.return_value = {
        "subject": "Application", "salutation": "Dear Sir or Madam,",
        "body": {"paragraphs": ["p1", "p2"]},
        "closing": "Kind regards", "signature": "Kaile",
    }

    with patch("applire.services.cover_letter.AsyncSessionLocal") as sl, \
         patch("applire.services.cover_letter.get_provider", return_value=provider), \
         patch("applire.services.cover_letter.review_and_refine",
               new=AsyncMock(side_effect=lambda **kw: kw["draft"])), \
         patch("applire.services.cover_letter.LLM_REVIEW_MAX_RETRIES", 0), \
         patch("applire.services.cover_letter_pdf.render_pdf",
               new=AsyncMock(return_value=b"%PDF-fake")), \
         patch.object(ve, "select_vault_evidence", side_effect=spy):
        sl.return_value.__aenter__.return_value = db
        from applire.services.cover_letter import _render_cover_letter_background
        await _render_cover_letter_background(cl_id, None, job_id)

    assert captured, "the letter chain never called select_vault_evidence"
    return captured


@pytest.mark.asyncio
async def test_letter_chain_hands_the_stored_facet_to_the_selector(db):
    """The mutation check for the letter call site: drop the
    ``leadership_emphasis=`` argument in ``services/cover_letter.py`` and this
    fails, because the selector would receive the posting's weighting nowhere."""
    captured = await _letter_selector_kwargs(db, dict(_FACET))
    passed = captured["kwargs"].get("leadership_emphasis")
    assert passed == _FACET


@pytest.mark.asyncio
async def test_letter_chain_passes_none_for_a_posting_without_a_weighting(db):
    captured = await _letter_selector_kwargs(db, None)
    assert captured["kwargs"].get("leadership_emphasis") is None


# ── End-to-end: analyze_jd persists the facet ─────────────────────────────


_JD_LLM_RESPONSE = {
    "company_name": "NordPharm SE",
    "role_title": "Staff AI Engineer",
    "required_skills": ["retrieval pipeline"],
    "nice_to_have_skills": [],
    "keywords": ["AI"],
    "seniority_level": "Senior",
    "company_culture_signals": [],
    "language_requirement": "English (C1)",
    "berufsbild_code": None,
    "berufsbild_label": None,
}


@pytest.fixture(autouse=False)
def _no_jd_review(monkeypatch):
    """Single-shot provider: this file covers the extraction seam, not the
    review loop (tests/unit/test_review_job_analysis.py owns that)."""
    monkeypatch.setattr("applire.services.job.LLM_REVIEW_MAX_RETRIES", 0)


async def _analyze(db, payload):
    from applire.services.job import analyze_jd

    provider = AsyncMock()
    provider.aparse_json = AsyncMock(return_value={**_JD_LLM_RESPONSE, **payload})
    return await analyze_jd(_JD_TEXT, db, provider)


@pytest.mark.asyncio
async def test_analyze_jd_persists_the_facet_end_to_end(db, _no_jd_review):
    """The whole chain in one assertion: prompt payload → review loop → shape
    guard → floor → ORM column → response schema."""
    result = await _analyze(db, {"leadership_emphasis": dict(_FACET)})
    assert result.leadership_emphasis is not None
    assert result.leadership_emphasis.emphasis == "leadership_led"
    assert result.leadership_emphasis.quote == _QUOTE

    from applire.models.job import JobAnalysis
    row = await db.get(JobAnalysis, result.id)
    assert row.leadership_emphasis == {"emphasis": "leadership_led", "quote": _QUOTE}


@pytest.mark.asyncio
async def test_analyze_jd_stores_nothing_for_a_fabricated_quote(db, _no_jd_review):
    result = await _analyze(
        db,
        {"leadership_emphasis": {"emphasis": "leadership_led",
                                 "quote": "You will manage a team of 40."}},
    )
    assert result.leadership_emphasis is None


@pytest.mark.asyncio
async def test_analyze_jd_stores_nothing_when_the_model_omits_the_field(db, _no_jd_review):
    """A posting with no leadership responsibility, and every pre-#271 caller."""
    result = await _analyze(db, {})
    assert result.leadership_emphasis is None
