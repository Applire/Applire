# tests/unit/test_color_schemes.py
"""
Unit tests for the color scheme derivation service.
No Docker, no DB, no LLM.

Run:
    pytest tests/unit/test_color_schemes.py -v
"""
import uuid

import pytest
from applire.services.color_schemes import derive_scheme, _hex_to_hsl, _hsl_to_hex


class TestHexHslConversion:
    def test_round_trip_primary_blue(self):
        h, s, l = _hex_to_hsl("#1b4f72")
        result = _hsl_to_hex(h, s, l)
        assert result == "#1b4f72"

    def test_round_trip_white(self):
        h, s, l = _hex_to_hsl("#ffffff")
        result = _hsl_to_hex(h, s, l)
        assert result == "#ffffff"

    def test_round_trip_black(self):
        h, s, l = _hex_to_hsl("#000000")
        result = _hsl_to_hex(h, s, l)
        assert result == "#000000"


class TestDeriveScheme:
    EU_BLUE = dict(
        seed_primary="#1b4f72",
        seed_accent="#2a8f9d",
        seed_secondary="#c9a84c",
        surface_lightness=0.97,
    )

    def test_returns_all_required_variables(self):
        derived = derive_scheme(**self.EU_BLUE)
        expected_keys = {
            "--color-primary", "--color-primary-container",
            "--color-teal", "--color-teal-dim", "--color-teal-container",
            "--color-teal-container-light",
            "--color-gold", "--color-gold-dim", "--color-gold-container",
            "--color-surface-dim", "--color-surface-bright",
            "--color-surface-container", "--color-surface-container-high",
            "--color-surface-container-highest",
            "--color-neutral-light",
        }
        assert set(derived.keys()) == expected_keys

    def test_seeds_pass_through(self):
        derived = derive_scheme(**self.EU_BLUE)
        assert derived["--color-primary"] == "#1b4f72"
        assert derived["--color-teal"] == "#2a8f9d"
        assert derived["--color-gold"] == "#c9a84c"

    def test_surface_bright_is_always_white(self):
        derived = derive_scheme(**self.EU_BLUE)
        assert derived["--color-surface-bright"] == "#ffffff"

    def test_all_values_are_valid_hex(self):
        import re
        hex_re = re.compile(r"^#[0-9a-f]{6}$")
        derived = derive_scheme(**self.EU_BLUE)
        for key, value in derived.items():
            assert hex_re.match(value), f"{key}: {value!r} is not a valid hex color"

    def test_container_is_lighter_than_seed(self):
        derived = derive_scheme(**self.EU_BLUE)
        _, _, l_seed = _hex_to_hsl(self.EU_BLUE["seed_primary"])
        _, _, l_container = _hex_to_hsl(derived["--color-primary-container"])
        assert l_container > l_seed

    def test_dim_is_darker_than_seed(self):
        derived = derive_scheme(**self.EU_BLUE)
        _, _, l_seed = _hex_to_hsl(self.EU_BLUE["seed_accent"])
        _, _, l_dim = _hex_to_hsl(derived["--color-teal-dim"])
        assert l_dim < l_seed

    def test_surface_container_high_is_darker_than_surface_dim(self):
        derived = derive_scheme(**self.EU_BLUE)
        _, _, l_dim = _hex_to_hsl(derived["--color-surface-dim"])
        _, _, l_high = _hex_to_hsl(derived["--color-surface-container-high"])
        assert l_high < l_dim

    def test_lower_surface_lightness_produces_darker_surfaces(self):
        light = derive_scheme(seed_primary="#1b4f72", seed_accent="#2a8f9d",
                              seed_secondary="#c9a84c", surface_lightness=0.97)
        dark = derive_scheme(seed_primary="#1b4f72", seed_accent="#2a8f9d",
                             seed_secondary="#c9a84c", surface_lightness=0.88)
        _, _, l_light = _hex_to_hsl(light["--color-surface-dim"])
        _, _, l_dark = _hex_to_hsl(dark["--color-surface-dim"])
        assert l_dark < l_light


# --- DB service tests (SQLite in-memory) ---

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from applire.db.session import Base
import applire.models.color_scheme  # noqa: F401 — registers model with Base.metadata


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def eu_blue(db):
    """Insert the EU Blue builtin scheme into the test DB."""
    from applire.models.color_scheme import ColorScheme
    from applire.services.color_schemes import derive_scheme
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
    db.add(scheme)
    await db.commit()
    await db.refresh(scheme)
    return scheme


class TestServiceFunctions:
    @pytest.mark.asyncio
    async def test_get_active_scheme_returns_eu_blue(self, db, eu_blue):
        from applire.services.color_schemes import get_active_scheme
        result = await get_active_scheme(db)
        assert result is not None
        assert result.name == "EU Blue"

    @pytest.mark.asyncio
    async def test_get_active_scheme_falls_back_to_first_when_none_active(self, db):
        """When no scheme is marked is_active (drifted or freshly-reset DB), fall
        back to the oldest scheme — the first entry in the list — so the UI always
        has a palette to apply instead of rendering unthemed."""
        from datetime import datetime, timezone
        from applire.models.color_scheme import ColorScheme
        from applire.services.color_schemes import get_active_scheme, list_schemes

        older = ColorScheme(
            id=uuid.uuid4(), name="Older", is_active=False, is_builtin=True,
            seed_primary="#112233", seed_accent="#445566", seed_secondary="#778899",
            surface_lightness=0.95, derived=derive_scheme("#112233", "#445566", "#778899", 0.95),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        newer = ColorScheme(
            id=uuid.uuid4(), name="Newer", is_active=False, is_builtin=True,
            seed_primary="#aabbcc", seed_accent="#ddeeff", seed_secondary="#001122",
            surface_lightness=0.95, derived=derive_scheme("#aabbcc", "#ddeeff", "#001122", 0.95),
            created_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
        )
        db.add_all([newer, older])  # add out of order — ordering must come from created_at
        await db.commit()

        result = await get_active_scheme(db)
        assert result is not None
        assert result.name == "Older"
        # Must match the first entry of the ordered list the admin UI renders.
        schemes = await list_schemes(db)
        assert result.id == schemes[0].id

    @pytest.mark.asyncio
    async def test_get_active_scheme_returns_none_when_no_schemes(self, db):
        """No schemes at all → None (nothing to fall back to)."""
        from applire.services.color_schemes import get_active_scheme
        assert await get_active_scheme(db) is None

    @pytest.mark.asyncio
    async def test_create_scheme_derives_values(self, db, eu_blue):
        from applire.services.color_schemes import create_scheme
        scheme = await create_scheme(
            db,
            name="Midnight",
            seed_primary="#1a1a2e",
            seed_accent="#16213e",
            seed_secondary="#e94560",
            surface_lightness=0.96,
        )
        assert scheme.name == "Midnight"
        assert "--color-primary" in scheme.derived
        assert scheme.is_active is False

    @pytest.mark.asyncio
    async def test_activate_scheme_deactivates_others(self, db, eu_blue):
        from applire.services.color_schemes import create_scheme, activate_scheme, get_active_scheme
        midnight = await create_scheme(
            db, name="Midnight", seed_primary="#1a1a2e",
            seed_accent="#16213e", seed_secondary="#e94560", surface_lightness=0.96,
        )
        activated = await activate_scheme(db, midnight.id)
        assert activated is not None
        assert activated.is_active is True
        # EU Blue should now be inactive
        active = await get_active_scheme(db)
        assert active is not None
        assert active.name == "Midnight"

    @pytest.mark.asyncio
    async def test_delete_scheme_removes_it(self, db, eu_blue):
        from applire.services.color_schemes import create_scheme, delete_scheme, list_schemes
        scheme = await create_scheme(
            db, name="ToDelete", seed_primary="#aabbcc",
            seed_accent="#112233", seed_secondary="#ddeeff", surface_lightness=0.95,
        )
        await delete_scheme(db, scheme.id)
        schemes = await list_schemes(db)
        assert not any(s.name == "ToDelete" for s in schemes)

    @pytest.mark.asyncio
    async def test_delete_builtin_raises(self, db, eu_blue):
        from applire.services.color_schemes import delete_scheme, SchemeIsBuiltin
        with pytest.raises(SchemeIsBuiltin):
            await delete_scheme(db, uuid.UUID("a0000000-0000-0000-0000-000000000001"))

    @pytest.mark.asyncio
    async def test_delete_active_raises(self, db, eu_blue):
        from applire.services.color_schemes import create_scheme, activate_scheme, delete_scheme, SchemeIsActive
        # Create and activate a non-builtin scheme
        scheme = await create_scheme(
            db, name="Active", seed_primary="#aabbcc",
            seed_accent="#112233", seed_secondary="#ddeeff", surface_lightness=0.95,
        )
        await activate_scheme(db, scheme.id)
        # Now scheme is active (not builtin), should raise SchemeIsActive
        with pytest.raises(SchemeIsActive):
            await delete_scheme(db, scheme.id)


# --- Migration 0030: re-color the mislabeled EU Blue built-in (GH #30) ---

class TestEuBlueRecolorMigration:
    """The original 0021 seed labeled a murky slate-teal palette "EU Blue".
    Migration 0030 corrects it to the canonical Continental Excellence EU
    palette (#003399 primary / #fecb00 gold, per docs/DESIGN.md). The shipped
    `derived` map must stay byte-consistent with derive_scheme() so the seed
    never drifts from the algorithm the editor/preview use."""

    def _migration(self):
        import importlib.util
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[2]
            / "backend" / "alembic" / "versions" / "0030_recolor_eu_blue.py"
        )
        spec = importlib.util.spec_from_file_location("migration_0030", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_seeds_are_canonical_eu_palette(self):
        m = self._migration()
        assert m._EU_BLUE_SEEDS == ("#003399", "#3557bc", "#fecb00")

    def test_derived_is_consistent_with_derive_scheme(self):
        m = self._migration()
        sp, sa, ss = m._EU_BLUE_SEEDS
        assert m._EU_BLUE_DERIVED == derive_scheme(sp, sa, ss, m._EU_BLUE_SURFACE_LIGHTNESS)

    def test_derived_carries_eu_blue_and_gold(self):
        m = self._migration()
        assert m._EU_BLUE_DERIVED["--color-primary"] == "#003399"
        assert m._EU_BLUE_DERIVED["--color-gold"] == "#fecb00"
