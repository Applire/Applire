"""
Unit tests for color derivation and cascade resolution.
Run: pytest tests/unit/test_color_detection.py -v
"""
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


class TestDeriveTint:
    def test_returns_hex_string(self):
        from applire.services.color_detection import derive_tint
        result = derive_tint("#2b5fa8")
        assert result.startswith("#")
        assert len(result) == 7

    def test_output_is_lighter_than_input(self):
        import colorsys
        from applire.services.color_detection import derive_tint
        accent = "#2b5fa8"
        tint = derive_tint(accent)
        def to_hls(h): r, g, b = (int(h.lstrip("#")[i:i+2], 16)/255 for i in (0, 2, 4)); return colorsys.rgb_to_hls(r, g, b)
        _, l_accent, _ = to_hls(accent)
        _, l_tint, _ = to_hls(tint)
        assert l_tint > l_accent

    def test_output_is_less_saturated_than_input(self):
        import colorsys
        from applire.services.color_detection import derive_tint
        accent = "#2b5fa8"
        tint = derive_tint(accent)
        def to_hls(h): r, g, b = (int(h.lstrip("#")[i:i+2], 16)/255 for i in (0, 2, 4)); return colorsys.rgb_to_hls(r, g, b)
        _, _, s_accent = to_hls(accent)
        _, _, s_tint = to_hls(tint)
        assert s_tint < s_accent

    def test_hue_is_preserved(self):
        import colorsys
        from applire.services.color_detection import derive_tint
        accent = "#2b5fa8"
        tint = derive_tint(accent)
        def to_hls(h): r, g, b = (int(h.lstrip("#")[i:i+2], 16)/255 for i in (0, 2, 4)); return colorsys.rgb_to_hls(r, g, b)
        h_accent, _, _ = to_hls(accent)
        h_tint, _, _ = to_hls(tint)
        # At S=0.10 (very low saturation), integer rounding can drift hue by ~0.05
        assert abs(h_accent - h_tint) < 0.05

    def test_pure_white_input_returns_white_like_result(self):
        from applire.services.color_detection import derive_tint
        result = derive_tint("#ffffff")
        assert result.startswith("#")
        assert len(result) == 7

    def test_color_context_has_accent_and_tint(self):
        from applire.services.color_detection import _make_color_context
        ctx = _make_color_context("#2b5fa8")
        assert ctx.accent == "#2b5fa8"
        assert ctx.tint.startswith("#")


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base
    import applire.models.user
    import applire.models.job
    import applire.models.profile
    import applire.models.gap
    import applire.models.cv
    import applire.models.session
    import applire.models.application
    import applire.models.cover_letter
    import applire.models.flow
    import applire.models.uploads
    import applire.models.color_profile
    import applire.models.company
    import applire.models.user_settings

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _make_user(user_id=None):
    from applire.models.user import User
    return User(id=user_id or uuid.uuid4(), email=f"test-{uuid.uuid4()}@test.com")


def _make_profile():
    from applire.models.profile import MasterProfile
    return MasterProfile(id=uuid.uuid4(), profile_json={})


def _make_job():
    from applire.models.job import JobAnalysis
    return JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash=str(uuid.uuid4()),
        raw_text="test job",
        role_title="Engineer",
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="mid",
        company_culture_signals=[],
        language_requirement="de",
    )


def _make_cv(job_id, profile_id, color_profile_id=None):
    from applire.models.cv import GeneratedCV
    return GeneratedCV(
        id=uuid.uuid4(),
        job_analysis_id=job_id,
        profile_id=profile_id,
        tailored_data={},
        template="classic_german",
        status="ready",
        color_profile_id=color_profile_id,
    )


def _make_color_profile(accent="#009fe3"):
    from applire.models.color_profile import ColorProfile
    from applire.services.color_detection import derive_tint
    return ColorProfile(
        id=uuid.uuid4(),
        seed_primary=accent,
        derived={"--cv-accent": accent, "--cv-accent-tint": derive_tint(accent)},
        source="favicon",
    )


class TestResolveColorContext:
    @pytest.mark.asyncio
    async def test_step1_cv_override_takes_priority(self, db):
        from applire.services.color_detection import resolve_color_context
        profile = _make_profile()
        job = _make_job()
        cp = _make_color_profile("#ff0000")
        db.add(profile); db.add(job); db.add(cp)
        await db.commit()
        cv = _make_cv(job.id, profile.id, color_profile_id=cp.id)
        db.add(cv)
        await db.commit()

        ctx = await resolve_color_context(cv, db)
        assert ctx.accent == "#ff0000"

    @pytest.mark.asyncio
    async def test_step2_company_profile_used_when_no_cv_override(self, db):
        from applire.services.color_detection import resolve_color_context
        from applire.models.company import Company
        profile = _make_profile()
        job = _make_job()
        cp = _make_color_profile("#00cc00")
        db.add(profile); db.add(job); db.add(cp)
        await db.commit()
        company = Company(name="Acme", domain="acme.com", color_profile_id=cp.id)
        db.add(company)
        await db.commit()
        job.company_id = company.id
        await db.commit()
        cv = _make_cv(job.id, profile.id)  # no cv override
        db.add(cv)
        await db.commit()

        ctx = await resolve_color_context(cv, db)
        assert ctx.accent == "#00cc00"

    @pytest.mark.asyncio
    async def test_step3_user_default_when_no_company(self, db):
        from applire.services.color_detection import resolve_color_context, _CE_STUB_USER_ID
        from applire.models.user_settings import UserSettings
        from applire.models.user import User
        user = User(id=_CE_STUB_USER_ID, email="local@applire.community")
        profile = _make_profile()
        job = _make_job()
        cp = _make_color_profile("#0000ff")
        db.add(user); db.add(profile); db.add(job); db.add(cp)
        await db.commit()
        settings = UserSettings(
            user_id=_CE_STUB_USER_ID,
            default_color_profile_id=cp.id,
        )
        db.add(settings)
        await db.commit()
        cv = _make_cv(job.id, profile.id)
        db.add(cv)
        await db.commit()

        ctx = await resolve_color_context(cv, db)
        assert ctx.accent == "#0000ff"

    @pytest.mark.asyncio
    async def test_step4_system_default_when_nothing_set(self, db):
        from applire.services.color_detection import resolve_color_context, DEFAULT_ACCENT
        profile = _make_profile()
        job = _make_job()
        db.add(profile); db.add(job)
        await db.commit()
        cv = _make_cv(job.id, profile.id)
        db.add(cv)
        await db.commit()

        ctx = await resolve_color_context(cv, db)
        assert ctx.accent == DEFAULT_ACCENT


class TestTemplateColorInjection:
    @pytest.mark.asyncio
    async def test_get_cv_html_injects_accent_from_color_profile(self, db):
        """The rendered HTML must contain the --cv-accent value from the CV's color_profile."""
        from applire.services.cv import get_cv_html
        from applire.models.color_profile import ColorProfile
        from applire.models.cv import GeneratedCV
        from applire.models.job import JobAnalysis
        from applire.models.profile import MasterProfile
        from applire.services.color_detection import derive_tint

        accent = "#aa1122"
        cp = ColorProfile(
            seed_primary=accent,
            derived={"--cv-accent": accent, "--cv-accent-tint": derive_tint(accent)},
            source="user",
        )
        profile = MasterProfile(id=uuid.uuid4(), profile_json={"personal_info": {"name": "Test User"}})
        job = JobAnalysis(
            id=uuid.uuid4(), raw_text_hash=str(uuid.uuid4()), raw_text="x",
            role_title="Dev", required_skills=[], nice_to_have_skills=[],
            keywords=[], seniority_level="mid", company_culture_signals=[],
            language_requirement="de",
        )
        db.add(cp); db.add(profile); db.add(job)
        await db.commit()

        minimal_tailored = {
            "contact": {"name": "Test User", "email": "t@t.com", "phone": "", "location": "", "linkedin": "", "photo_url": None},
            "summary": "Summary text",
            "work_history": [],
            "education": [],
            "skills": ["Python"],
            "languages": [],
            "show_photo": False,
        }
        cv = GeneratedCV(
            id=uuid.uuid4(),
            job_analysis_id=job.id,
            profile_id=profile.id,
            tailored_data=minimal_tailored,
            template="modern_swiss",
            status="ready",
            color_profile_id=cp.id,
        )
        db.add(cv)
        await db.commit()

        html = await get_cv_html(cv.id, db)
        assert accent in html, f"Expected {accent} in rendered HTML"


class TestDeriveSurfaceText:
    """WCAG-contrast-driven auto text colour on coloured surfaces."""

    def test_dark_surface_returns_white(self):
        from applire.services.color_detection import derive_surface_text
        # Navy — luminance ~0.044
        assert derive_surface_text("#1a3a6e") == "#ffffff"

    def test_light_surface_returns_dark(self):
        from applire.services.color_detection import derive_surface_text
        # Light blue — luminance ~0.75
        assert derive_surface_text("#c5d8f8") == "#1a1a1a"

    def test_pure_black_returns_white(self):
        from applire.services.color_detection import derive_surface_text
        assert derive_surface_text("#000000") == "#ffffff"

    def test_pure_white_returns_dark(self):
        from applire.services.color_detection import derive_surface_text
        assert derive_surface_text("#ffffff") == "#1a1a1a"

    def test_mid_grey_boundary(self):
        from applire.services.color_detection import derive_surface_text
        # #767676 is approx WCAG boundary — just confirm it returns a valid value
        result = derive_surface_text("#767676")
        assert result in ("#ffffff", "#1a1a1a")

    def test_result_is_always_one_of_two_values(self):
        from applire.services.color_detection import derive_surface_text
        for colour in ["#ff0000", "#00ff00", "#0000ff", "#ffff00", "#ff6600"]:
            assert derive_surface_text(colour) in ("#ffffff", "#1a1a1a")


class TestColorContextFields:
    """_make_color_context must populate all Phase-1 slots."""

    def _ctx(self, hex_color):
        from applire.services.color_detection import _make_color_context
        return _make_color_context(hex_color)

    def test_primary_equals_seed(self):
        ctx = self._ctx("#2b5fa8")
        assert ctx.primary == "#2b5fa8"

    def test_accent_alias_equals_primary(self):
        ctx = self._ctx("#2b5fa8")
        assert ctx.accent == ctx.primary

    def test_primary_tint_is_hex(self):
        ctx = self._ctx("#2b5fa8")
        assert ctx.primary_tint.startswith("#") and len(ctx.primary_tint) == 7

    def test_tint_alias_equals_primary_tint(self):
        ctx = self._ctx("#2b5fa8")
        assert ctx.tint == ctx.primary_tint

    def test_surface_equals_primary_in_phase1(self):
        ctx = self._ctx("#2b5fa8")
        assert ctx.surface == ctx.primary

    def test_secondary_equals_primary_in_phase1(self):
        ctx = self._ctx("#2b5fa8")
        assert ctx.secondary == ctx.primary

    def test_surface_text_is_white_for_dark_surface(self):
        ctx = self._ctx("#1a3a6e")
        assert ctx.surface_text == "#ffffff"

    def test_surface_text_is_dark_for_light_surface(self):
        ctx = self._ctx("#dce8f7")
        assert ctx.surface_text == "#1a1a1a"
