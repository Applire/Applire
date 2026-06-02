"""
Unit tests for the admin color scheme router and Pydantic schemas.
Uses FastAPI AsyncClient with an in-memory SQLite database.
No Docker required.

Run:
    pytest tests/unit/test_color_scheme_router.py -v
"""
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

from applire.db.session import Base, get_db
from applire.models.color_scheme import ColorScheme  # noqa: F401 — registers with Base
from applire.routers.admin.color_schemes import router
from applire.services.color_schemes import derive_scheme

# ---------------------------------------------------------------------------
# App + DB fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def app(db_session):
    """FastAPI test app with the admin router and an overridden DB dependency."""
    _app = FastAPI()
    _app.include_router(router)
    _app.dependency_overrides[get_db] = lambda: db_session
    return _app


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def eu_blue(db_session):
    """Insert the EU Blue builtin scheme."""
    scheme = ColorScheme(
        id=uuid.UUID("a0000000-0000-0000-0000-000000000001"),
        name="EU Blue",
        is_active=True,
        is_builtin=True,
        seed_primary="#1b4f72",
        seed_accent="#2a8f9d",
        seed_secondary="#c9a84c",
        surface_lightness=0.97,
        derived=derive_scheme("#1b4f72", "#2a8f9d", "#c9a84c", 0.97),
    )
    db_session.add(scheme)
    await db_session.commit()
    await db_session.refresh(scheme)
    return scheme


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------

class TestColorSchemeSchemas:
    def test_create_schema_valid(self):
        from applire.schemas.color_scheme import ColorSchemeCreate
        obj = ColorSchemeCreate(
            name="Test",
            seed_primary="#1B4F72",
            seed_accent="#2A8F9D",
            seed_secondary="#C9A84C",
            surface_lightness=0.97,
        )
        assert obj.seed_primary == "#1b4f72"  # normalised to lowercase

    def test_create_schema_invalid_hex(self):
        from applire.schemas.color_scheme import ColorSchemeCreate
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            ColorSchemeCreate(
                name="Test", seed_primary="notahex",
                seed_accent="#2A8F9D", seed_secondary="#C9A84C",
            )

    def test_create_schema_lightness_out_of_range(self):
        from applire.schemas.color_scheme import ColorSchemeCreate
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            ColorSchemeCreate(
                name="Test", seed_primary="#1B4F72",
                seed_accent="#2A8F9D", seed_secondary="#C9A84C",
                surface_lightness=1.5,
            )

    def test_create_schema_accepts_lower_saturation_bound(self):
        # The surface-tint lower bound was relaxed from 0.88 to 0.50 so admins
        # can pick noticeably more saturated backgrounds.
        from applire.schemas.color_scheme import ColorSchemeCreate
        obj = ColorSchemeCreate(
            name="Test", seed_primary="#1B4F72",
            seed_accent="#2A8F9D", seed_secondary="#C9A84C",
            surface_lightness=0.50,
        )
        assert obj.surface_lightness == 0.50

    def test_create_schema_rejects_below_lower_bound(self):
        from applire.schemas.color_scheme import ColorSchemeCreate
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            ColorSchemeCreate(
                name="Test", seed_primary="#1B4F72",
                seed_accent="#2A8F9D", seed_secondary="#C9A84C",
                surface_lightness=0.49,
            )

    def test_create_schema_empty_name(self):
        from applire.schemas.color_scheme import ColorSchemeCreate
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            ColorSchemeCreate(
                name="   ", seed_primary="#1B4F72",
                seed_accent="#2A8F9D", seed_secondary="#C9A84C",
            )

    def test_create_schema_name_too_long(self):
        from applire.schemas.color_scheme import ColorSchemeCreate
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            ColorSchemeCreate(
                name="x" * 65, seed_primary="#1B4F72",
                seed_accent="#2A8F9D", seed_secondary="#C9A84C",
            )

    def test_preview_schema_valid(self):
        from applire.schemas.color_scheme import ColorSchemePreviewRequest
        obj = ColorSchemePreviewRequest(
            seed_primary="#1B4F72",
            seed_accent="#2A8F9D",
            seed_secondary="#C9A84C",
        )
        assert obj.surface_lightness == 0.97  # default (refined in sprint-23)

    def test_preview_schema_invalid_lightness(self):
        from applire.schemas.color_scheme import ColorSchemePreviewRequest
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            ColorSchemePreviewRequest(
                seed_primary="#1B4F72", seed_accent="#2A8F9D",
                seed_secondary="#C9A84C", surface_lightness=1.5,
            )


# ---------------------------------------------------------------------------
# Router endpoint tests
# ---------------------------------------------------------------------------

class TestColorSchemeRouter:

    @pytest.mark.asyncio
    async def test_get_active_returns_eu_blue(self, client, eu_blue):
        res = await client.get("/api/admin/color-schemes/active")
        assert res.status_code == 200
        data = res.json()
        assert data["name"] == "EU Blue"
        assert "--color-primary" in data["derived"]

    @pytest.mark.asyncio
    async def test_get_active_404_when_none(self, client):
        res = await client.get("/api/admin/color-schemes/active")
        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_list_schemes_returns_eu_blue(self, client, eu_blue):
        res = await client.get("/api/admin/color-schemes")
        assert res.status_code == 200
        schemes = res.json()
        assert len(schemes) == 1
        assert schemes[0]["name"] == "EU Blue"

    @pytest.mark.asyncio
    async def test_preview_returns_derived(self, client):
        res = await client.post("/api/admin/color-schemes/preview", json={
            "seed_primary": "#1b4f72",
            "seed_accent": "#2a8f9d",
            "seed_secondary": "#c9a84c",
            "surface_lightness": 0.97,
        })
        assert res.status_code == 200
        data = res.json()
        assert "--color-primary" in data
        assert len(data) == 15

    @pytest.mark.asyncio
    async def test_create_scheme_returns_201(self, client, eu_blue):
        res = await client.post("/api/admin/color-schemes", json={
            "name": "Midnight",
            "seed_primary": "#1a1a2e",
            "seed_accent": "#16213e",
            "seed_secondary": "#e94560",
            "surface_lightness": 0.96,
        })
        assert res.status_code == 201
        data = res.json()
        assert data["name"] == "Midnight"
        assert data["is_active"] is False

    @pytest.mark.asyncio
    async def test_activate_scheme(self, client, eu_blue, db_session):
        # Create a second scheme first
        create_res = await client.post("/api/admin/color-schemes", json={
            "name": "Midnight",
            "seed_primary": "#1a1a2e",
            "seed_accent": "#16213e",
            "seed_secondary": "#e94560",
            "surface_lightness": 0.96,
        })
        assert create_res.status_code == 201
        scheme_id = create_res.json()["id"]
        activate_res = await client.patch(f"/api/admin/color-schemes/{scheme_id}/activate")
        assert activate_res.status_code == 200
        assert activate_res.json()["is_active"] is True

    @pytest.mark.asyncio
    async def test_activate_404_not_found(self, client):
        fake_id = str(uuid.uuid4())
        res = await client.patch(f"/api/admin/color-schemes/{fake_id}/activate")
        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_builtin_returns_409(self, client, eu_blue):
        res = await client.delete(
            f"/api/admin/color-schemes/a0000000-0000-0000-0000-000000000001"
        )
        assert res.status_code == 409

    @pytest.mark.asyncio
    async def test_delete_active_returns_409(self, client, eu_blue, db_session):
        create_res = await client.post("/api/admin/color-schemes", json={
            "name": "Active",
            "seed_primary": "#aabbcc",
            "seed_accent": "#112233",
            "seed_secondary": "#ddeeff",
            "surface_lightness": 0.95,
        })
        scheme_id = create_res.json()["id"]
        await client.patch(f"/api/admin/color-schemes/{scheme_id}/activate")
        res = await client.delete(f"/api/admin/color-schemes/{scheme_id}")
        assert res.status_code == 409

    @pytest.mark.asyncio
    async def test_delete_not_found_returns_404(self, client):
        fake_id = str(uuid.uuid4())
        res = await client.delete(f"/api/admin/color-schemes/{fake_id}")
        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_non_active_non_builtin_returns_204(self, client, eu_blue):
        create_res = await client.post("/api/admin/color-schemes", json={
            "name": "ToDelete",
            "seed_primary": "#aabbcc",
            "seed_accent": "#112233",
            "seed_secondary": "#ddeeff",
            "surface_lightness": 0.95,
        })
        scheme_id = create_res.json()["id"]
        res = await client.delete(f"/api/admin/color-schemes/{scheme_id}")
        assert res.status_code == 204
