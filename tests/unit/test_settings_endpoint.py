"""
Unit tests for GET/PATCH /api/settings.
Run: pytest tests/unit/test_settings_endpoint.py -v
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
    import applire.models.flow
    import applire.models.uploads
    import applire.models.color_profile
    import applire.models.company
    import applire.models.cover_letter
    import applire.models.user_settings
    from applire.models.user import User
    from applire.services.color_detection import _CE_STUB_USER_ID

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        # Insert stub user (CE always has this user)
        user = User(id=_CE_STUB_USER_ID, email="local@applire.community")
        session.add(user)
        await session.commit()
        yield session
    await engine.dispose()


class TestSettingsEndpoint:
    @pytest.mark.asyncio
    async def test_get_settings_returns_null_defaults_when_no_row(self, db):
        from applire.routers.settings import get_settings
        result = await get_settings(db)
        assert result["default_accent_hex"] is None

    @pytest.mark.asyncio
    async def test_patch_settings_stores_default_color(self, db):
        from applire.routers.settings import update_settings, get_settings
        await update_settings(db, accent_hex="#334455")
        result = await get_settings(db)
        assert result["default_accent_hex"] == "#334455"

    @pytest.mark.asyncio
    async def test_patch_settings_raises_on_invalid_hex(self, db):
        from applire.routers.settings import update_settings
        with pytest.raises(ValueError):
            await update_settings(db, accent_hex="not-hex")

    @pytest.mark.asyncio
    async def test_patch_settings_updates_existing_row(self, db):
        from applire.routers.settings import update_settings, get_settings
        await update_settings(db, accent_hex="#aabbcc")
        await update_settings(db, accent_hex="#112233")
        result = await get_settings(db)
        assert result["default_accent_hex"] == "#112233"


class TestLanguageSettings:
    @pytest.mark.asyncio
    async def test_get_settings_defaults_to_english_when_no_row(self, db):
        # ADR-038: ui_language is NOT NULL; no row → default 'en'
        from applire.routers.settings import get_settings
        result = await get_settings(db)
        assert result["ui_language"] == "en"

    @pytest.mark.asyncio
    async def test_get_settings_returns_persisted_language(self, db):
        # Row with ui_language='de' should be returned as-is
        from applire.routers.settings import get_settings, update_settings
        await update_settings(db, ui_language="de")
        result = await get_settings(db)
        assert result["ui_language"] == "de"

    @pytest.mark.asyncio
    async def test_patch_settings_stores_ui_language(self, db):
        from applire.routers.settings import update_settings, get_settings
        await update_settings(db, ui_language="de")
        result = await get_settings(db)
        assert result["ui_language"] == "de"

    @pytest.mark.asyncio
    async def test_patch_settings_rejects_invalid_language(self, db):
        from applire.routers.settings import update_settings
        with pytest.raises(ValueError, match="ui_language"):
            await update_settings(db, ui_language="zh")

    @pytest.mark.asyncio
    async def test_patch_settings_updates_both_language_and_color(self, db):
        from applire.routers.settings import update_settings, get_settings
        await update_settings(db, accent_hex="#aabbcc", ui_language="en")
        result = await get_settings(db)
        assert result["default_accent_hex"] == "#aabbcc"
        assert result["ui_language"] == "en"

    @pytest.mark.asyncio
    async def test_update_settings_empty_patch_new_row_defaults_en(self, db):
        # Regression: empty PATCH when no row exists must not crash with None ui_language
        # (DB server_default='en' is not reflected in-memory before commit; guard with `or "en"`)
        from applire.routers.settings import update_settings
        result = await update_settings(db)  # no accent_hex, no ui_language
        assert result["ui_language"] == "en"

    # --- ADR-038 amendment 2026-08-01 (#400/#313): the 'en' default is not a choice ---

    @pytest.mark.asyncio
    async def test_get_settings_not_explicit_when_no_row(self, db):
        from applire.routers.settings import get_settings
        result = await get_settings(db)
        assert result["ui_language"] == "en"
        assert result["ui_language_explicit"] is False

    @pytest.mark.asyncio
    async def test_language_write_marks_explicit(self, db):
        from applire.routers.settings import get_settings, update_settings
        await update_settings(db, ui_language="de")
        result = await get_settings(db)
        assert result["ui_language"] == "de"
        assert result["ui_language_explicit"] is True

    @pytest.mark.asyncio
    async def test_explicit_en_choice_is_explicit(self, db):
        from applire.routers.settings import get_settings, update_settings
        await update_settings(db, ui_language="en")
        result = await get_settings(db)
        assert result["ui_language"] == "en"
        assert result["ui_language_explicit"] is True

    @pytest.mark.asyncio
    async def test_unrelated_write_does_not_make_language_explicit(self, db):
        # A row auto-created by a non-language settings write must not count as
        # a language choice — row-existence is not choice-existence (#400).
        from applire.routers.settings import get_settings, update_settings
        await update_settings(db, accent_hex="#334455")
        result = await get_settings(db)
        assert result["ui_language"] == "en"
        assert result["ui_language_explicit"] is False


class TestPreDownloadNoticeSetting:
    # ADR-040 amendment (2026-07-01): one shared user-level flag suppresses the
    # clean-case pre-download notice across BOTH the CV and cover-letter surfaces.
    @pytest.mark.asyncio
    async def test_get_settings_defaults_notice_visible_when_no_row(self, db):
        from applire.routers.settings import get_settings
        result = await get_settings(db)
        assert result["hide_predownload_notice"] is False

    @pytest.mark.asyncio
    async def test_patch_stores_and_returns_hide_predownload_notice(self, db):
        from applire.routers.settings import update_settings, get_settings
        await update_settings(db, hide_predownload_notice=True)
        result = await get_settings(db)
        assert result["hide_predownload_notice"] is True

    @pytest.mark.asyncio
    async def test_patch_can_re_enable_the_notice(self, db):
        from applire.routers.settings import update_settings, get_settings
        await update_settings(db, hide_predownload_notice=True)
        await update_settings(db, hide_predownload_notice=False)
        result = await get_settings(db)
        assert result["hide_predownload_notice"] is False

    @pytest.mark.asyncio
    async def test_notice_flag_independent_of_other_fields(self, db):
        # Setting it must not disturb ui_language / color (partial PATCH).
        from applire.routers.settings import update_settings, get_settings
        await update_settings(db, ui_language="de")
        await update_settings(db, hide_predownload_notice=True)
        result = await get_settings(db)
        assert result["ui_language"] == "de"
        assert result["hide_predownload_notice"] is True


class TestTargetCvPagesSetting:
    # E042/US236 (ADR-051 §1): NULL = "use region standard"; per-generation
    # override still wins at generation time (Task 1.1 only persists the setting).
    @pytest.mark.asyncio
    async def test_get_settings_defaults_target_cv_pages_null_when_no_row(self, db):
        from applire.routers.settings import get_settings
        result = await get_settings(db)
        assert result["target_cv_pages"] is None

    @pytest.mark.asyncio
    async def test_patch_stores_and_returns_target_cv_pages(self, db):
        from applire.routers.settings import update_settings, get_settings
        await update_settings(db, target_cv_pages=3)
        result = await get_settings(db)
        assert result["target_cv_pages"] == 3

    @pytest.mark.asyncio
    async def test_patch_rejects_zero(self, db):
        from applire.routers.settings import update_settings
        with pytest.raises(ValueError, match="target_cv_pages"):
            await update_settings(db, target_cv_pages=0)

    @pytest.mark.asyncio
    async def test_patch_rejects_negative(self, db):
        from applire.routers.settings import update_settings
        with pytest.raises(ValueError, match="target_cv_pages"):
            await update_settings(db, target_cv_pages=-1)

    @pytest.mark.asyncio
    async def test_patch_accepts_value_above_region_norm(self, db):
        # No upper cap — users may deliberately exceed the DACH 2-page standard.
        from applire.routers.settings import update_settings, get_settings
        await update_settings(db, target_cv_pages=7)
        result = await get_settings(db)
        assert result["target_cv_pages"] == 7

    @pytest.mark.asyncio
    async def test_target_cv_pages_independent_of_other_fields(self, db):
        from applire.routers.settings import update_settings, get_settings
        await update_settings(db, ui_language="de")
        await update_settings(db, target_cv_pages=3)
        result = await get_settings(db)
        assert result["ui_language"] == "de"
        assert result["target_cv_pages"] == 3

    @pytest.mark.asyncio
    async def test_omitting_target_cv_pages_leaves_existing_value_untouched(self, db):
        # None at the service layer means "not provided" (same convention as
        # accent_hex/ui_language/hide_predownload_notice) — it is NOT a clear
        # request. Clearing back to "use region standard" is out of scope here.
        from applire.routers.settings import update_settings, get_settings
        await update_settings(db, target_cv_pages=3)
        await update_settings(db, ui_language="de")
        result = await get_settings(db)
        assert result["target_cv_pages"] == 3


class TestTargetCvPagesExplicitClear:
    # Whole-branch review Finding 1: the frontend's "Region standard" option
    # sends {"target_cv_pages": null} explicitly (distinct from omitting the
    # key). The PATCH route must distinguish explicit-null (clear) from
    # omitted (leave untouched) via Pydantic's model_fields_set. Exercised
    # over the real HTTP route since that's where the distinction is made.
    @pytest_asyncio.fixture
    async def client(self, db):
        from applire.auth import get_auth_provider
        from applire.db.session import get_db
        from applire.routers.settings import router
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from unittest.mock import MagicMock

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_auth_provider] = lambda: MagicMock()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    @pytest.mark.asyncio
    async def test_explicit_null_clears_stored_target_cv_pages(self, db, client):
        from applire.routers.settings import update_settings, get_settings

        await update_settings(db, target_cv_pages=3)
        resp = await client.patch("/api/settings", json={"target_cv_pages": None})
        assert resp.status_code == 200
        assert resp.json()["target_cv_pages"] is None

        result = await get_settings(db)
        assert result["target_cv_pages"] is None

    @pytest.mark.asyncio
    async def test_omitted_key_over_http_leaves_value_untouched(self, db, client):
        from applire.routers.settings import update_settings

        await update_settings(db, target_cv_pages=3)
        resp = await client.patch("/api/settings", json={"ui_language": "de"})
        assert resp.status_code == 200
        assert resp.json()["target_cv_pages"] == 3


class TestReviewModeSetting:
    # ADR-081 clause 5 (US301): a three-valued per-user preference —
    # auto | overview | guided, default 'auto'. 'auto' lets the document
    # decide (clause 5's own auto-resolution logic lives in the frontend);
    # 'overview'/'guided' are fixed user overrides. Additive column on the
    # 0042 migration pattern.
    @pytest_asyncio.fixture
    async def client(self, db):
        from applire.auth import get_auth_provider
        from applire.db.session import get_db
        from applire.routers.settings import router
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from unittest.mock import MagicMock

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_auth_provider] = lambda: MagicMock()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    @pytest.mark.asyncio
    async def test_get_settings_returns_auto_review_mode_when_no_row(self, db):
        from applire.routers.settings import get_settings

        result = await get_settings(db)
        assert result["review_mode"] == "auto"

    @pytest.mark.asyncio
    async def test_patch_stores_and_echoes_review_mode_and_a_later_get_reads_it(
        self, db, client
    ):
        from applire.routers.settings import get_settings

        resp = await client.patch("/api/settings", json={"review_mode": "guided"})
        assert resp.status_code == 200
        assert resp.json()["review_mode"] == "guided"

        result = await get_settings(db)
        assert result["review_mode"] == "guided"

    @pytest.mark.asyncio
    async def test_patch_rejects_invalid_review_mode_over_http(self, db, client):
        resp = await client.patch("/api/settings", json={"review_mode": "sideways"})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_update_settings_raises_on_invalid_review_mode(self, db):
        # Service-layer guard pinned independently of the Literal schema.
        from applire.routers.settings import update_settings

        with pytest.raises(ValueError, match="review_mode"):
            await update_settings(db, review_mode="sideways")

    @pytest.mark.asyncio
    async def test_omitting_review_mode_leaves_existing_value_untouched(
        self, db, client
    ):
        resp = await client.patch("/api/settings", json={"review_mode": "guided"})
        assert resp.status_code == 200

        resp = await client.patch("/api/settings", json={"ui_language": "de"})
        assert resp.status_code == 200
        assert resp.json()["review_mode"] == "guided"

    @pytest.mark.asyncio
    async def test_legacy_row_with_null_review_mode_served_as_auto(self, db):
        # A row created before this column existed must still serve 'auto',
        # never None. The column is NOT NULL at the DB level (server_default
        # backfills real rows on migration), so a literal NULL can never be
        # committed here — that's the same guarantee production gets. What
        # the defensive `getattr(row, "review_mode", None) or "auto"` in
        # get_settings() actually guards against is the transient in-Python
        # state before that default is applied (mirrors the existing
        # ui_language "not reflected in-memory before commit" comment a few
        # lines up). We force exactly that state directly via `__dict__`,
        # bypassing the instrumented setter so SQLAlchemy's autoflush does
        # not try (and fail) to persist a NULL.
        from applire.routers.settings import get_settings, update_settings
        from applire.models.user_settings import UserSettings
        from applire.services.color_detection import _CE_STUB_USER_ID
        from sqlalchemy import select

        await update_settings(db, ui_language="de")
        result = await db.execute(
            select(UserSettings).where(UserSettings.user_id == _CE_STUB_USER_ID)
        )
        row = result.scalar_one()
        row.__dict__["review_mode"] = None

        result = await get_settings(db)
        assert result["review_mode"] == "auto"

    def test_review_mode_not_exposed_on_mcp_tool_surface(self):
        # ADR-081 clause 8 / SF-DOOR.4 carve-out: review_mode governs
        # presentation only and is deliberately NOT exposed over MCP — an
        # ADR-054 BYOI agent consumes the structured report, never the
        # panel. Reuses the same tools/list mechanism as
        # tests/unit/test_mcp_agent_guide.py's serialized-surface test.
        import asyncio
        import json

        from applire.mcp.server import mcp

        tools = asyncio.run(mcp.list_tools())
        surface = json.dumps(
            [
                {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
                for t in tools
            ]
        )
        assert "review_mode" not in surface
