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
