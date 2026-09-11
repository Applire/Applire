# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""F-4b (founder ruling, 2026-09-11) — the per-document signature override.

On top of #359's two KIND-level defaults (``user_settings.signature_in_cv`` /
``signature_in_letter``), each generated CV and cover letter now carries its
own nullable ``signature_override`` column (migration 0066): ``None`` = use
the kind default (#359's existing behaviour, unchanged), ``True``/``False`` =
with/without, regardless of what the kind default says.

Covers, in order:

1. Migration 0066 (read, never executed via alembic — sqlite cannot run the
   full chain; see ``test_signature_service.py``'s own migration test for why).
2. The precedence itself at the ``services.signature`` layer — the ONE seam
   (``_signature_path_if_enabled``) every render path and every status
   response shares.
3. ``resolve_signature_effective`` / ``resolve_signature_available`` — the two
   (three, F-4b added ``signature_available`` — see the report for why)
   fields the status responses expose.
4. One seam test per render seam (four), each parametrized over: override
   wins ON despite the kind default OFF, override wins OFF despite the kind
   default ON, and ``None`` falls back to the kind default in both directions.
5. ``set_cv_signature_override`` / ``set_cover_letter_signature_override`` —
   the service functions behind the two PATCH endpoints.

House style follows ``test_signature_service.py`` (in-memory SQLite,
``LocalStorageProvider`` over a temp dir, ``monkeypatch`` for
``applire.storage.get_storage``) and ``tests/unit/test_cv_color_endpoint.py``
(service-level ``LookupError`` tests, ``_ready_cv``-style minimal fixtures).
"""
from __future__ import annotations

import io
import re
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base
    import applire.models.user  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.job  # noqa: F401
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
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def storage(tmp_path):
    from applire.storage.local import LocalStorageProvider

    return LocalStorageProvider(str(tmp_path))


async def _make_user(db) -> uuid.UUID:
    from applire.models.user import User

    user_id = uuid.uuid4()
    db.add(User(id=user_id, email=f"{user_id}@example.de"))
    await db.commit()
    return user_id


async def _settings_row(db):
    from applire.models.user_settings import UserSettings
    from applire.services.color_detection import _CE_STUB_USER_ID

    result = await db.execute(
        select(UserSettings).where(UserSettings.user_id == _CE_STUB_USER_ID)
    )
    return result.scalar_one_or_none()


def _real_png_bytes(color=(30, 60, 120)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 20), color).save(buf, format="PNG")
    return buf.getvalue()


async def _upload(db, storage, file_bytes=None) -> str:
    """Upload a real PNG signature and return the settings row's path."""
    from applire.services.signature import upload_signature

    user_id = await _make_user(db)
    result = await upload_signature(
        user_id=user_id,
        file_bytes=file_bytes or _real_png_bytes(),
        content_type="image/png",
        db=db,
        storage=storage,
    )
    return result["signature_url"]


async def _set_kind_default(db, *, letter: bool | None = None, cv: bool | None = None) -> None:
    row = await _settings_row(db)
    assert row is not None, "call _upload() first"
    if letter is not None:
        row.signature_in_letter = letter
    if cv is not None:
        row.signature_in_cv = cv
    await db.commit()


def _ready_cv(job_id, profile_id, *, signature_override=None, location="Berlin"):
    from applire.models.cv import GeneratedCV

    minimal = {
        "contact": {
            "name": "Anna Bauer", "email": "anna@example.de", "phone": "",
            "location": location, "linkedin": "", "photo_url": None,
        },
        "summary": "MARKER_SUMMARY", "work_history": [], "education": [],
        "skills": [], "languages": [], "show_photo": False,
    }
    return GeneratedCV(
        id=uuid.uuid4(), job_analysis_id=job_id, profile_id=profile_id,
        tailored_data=minimal, template="classic_german", status="ready",
        document_language="de",  # skips the job/application resolution branch
        signature_override=signature_override,
    )


def _ready_letter(job_id, profile_id, *, signature_override=None):
    from applire.models.cover_letter import GeneratedCoverLetter

    letter_data = {
        "header": {"name": "Anna Bauer"},
        "recipient": {"name": "Recruiting Team"},
        "body": {"paragraphs": ["MARKER_BODY_TEXT"]},
        "signature": {"closing": "Mit freundlichen Grüßen", "name": "Anna Bauer"},
    }
    return GeneratedCoverLetter(
        id=uuid.uuid4(), job_analysis_id=job_id, profile_id=profile_id,
        template="classic_german", letter_data=letter_data, status="ready",
        document_language="de",
        signature_override=signature_override,
    )


# ---------------------------------------------------------------------------
# 1. Migration 0066 — read the module, never run alembic
# ---------------------------------------------------------------------------


def _load_migration_0066():
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0066_signature_document_override.py"
    spec = importlib.util.spec_from_file_location(
        "_migration_0066_signature_document_override_under_test", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_0066_revision_chain_and_touches_exactly_two_columns():
    import inspect

    module = _load_migration_0066()
    assert module.revision == "0066"
    assert module.down_revision == "0065"

    upgrade_src = inspect.getsource(module.upgrade)
    downgrade_src = inspect.getsource(module.downgrade)

    added = re.findall(
        r'op\.add_column\(\s*"(\w+)",\s*sa\.Column\(\s*"(\w+)"', upgrade_src
    )
    dropped = re.findall(
        r'op\.drop_column\(\s*"(\w+)",\s*"(\w+)"\)', downgrade_src
    )

    expected = {("generated_cvs", "signature_override"), ("generated_cover_letters", "signature_override")}
    assert set(added) == expected and len(added) == 2
    assert set(dropped) == expected and len(dropped) == 2


@pytest.mark.asyncio
async def test_migration_0066_upgrade_and_downgrade_on_sqlite(db):
    """Executed (not merely read) against the in-memory sqlite engine bound
    to `db`, via Alembic's own ``Operations.context()`` (installs the real
    ``alembic.op`` global proxy the migration module calls) — mirrors the
    evidence contract WP-F's report used for 0065 (full-chain alembic cannot
    run on sqlite; JSONB predates it)."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect as sa_inspect

    module = _load_migration_0066()
    conn = await db.connection()

    def _run(sync_conn, fn):
        ctx = MigrationContext.configure(sync_conn)
        with Operations.context(ctx):
            fn()

    def _columns(sync_conn, table):
        return {c["name"] for c in sa_inspect(sync_conn).get_columns(table)}

    # The `db` fixture's Base.metadata.create_all already built BOTH tables
    # from the current ORM (which already declares signature_override) — the
    # opposite of the pre-0066 state this migration is meant to move FROM.
    # Drop the column first so upgrade() has something real to add (sqlite
    # 3.35+ supports DROP COLUMN).
    from sqlalchemy import text

    await conn.execute(text("ALTER TABLE generated_cvs DROP COLUMN signature_override"))
    await conn.execute(text("ALTER TABLE generated_cover_letters DROP COLUMN signature_override"))
    pre_cv_cols = await conn.run_sync(_columns, "generated_cvs")
    pre_cl_cols = await conn.run_sync(_columns, "generated_cover_letters")
    assert "signature_override" not in pre_cv_cols
    assert "signature_override" not in pre_cl_cols

    await conn.run_sync(_run, module.upgrade)
    cv_cols = await conn.run_sync(_columns, "generated_cvs")
    cl_cols = await conn.run_sync(_columns, "generated_cover_letters")
    assert "signature_override" in cv_cols
    assert "signature_override" in cl_cols

    await conn.run_sync(_run, module.downgrade)
    cv_cols_after = await conn.run_sync(_columns, "generated_cvs")
    cl_cols_after = await conn.run_sync(_columns, "generated_cover_letters")
    assert "signature_override" not in cv_cols_after
    assert "signature_override" not in cl_cols_after


# ---------------------------------------------------------------------------
# 2. Precedence at the services.signature layer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_override_true_wins_over_cv_kind_default_off(db, storage, monkeypatch):
    """CV's kind default is OFF (F-0). override=True must still render."""
    monkeypatch.setattr("applire.storage.get_storage", lambda: storage)
    from applire.services.signature import resolve_signature_data_uri

    await _upload(db, storage)
    row = await _settings_row(db)
    assert row.signature_in_cv is False, "sanity: the kind default really is off"

    result = await resolve_signature_data_uri(db, document="cv", override=True)
    assert result is not None
    assert result.startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_override_false_wins_over_letter_kind_default_on(db, storage, monkeypatch):
    """Letter's kind default is ON (F-0). override=False must still suppress it."""
    monkeypatch.setattr("applire.storage.get_storage", lambda: storage)
    from applire.services.signature import resolve_signature_data_uri

    await _upload(db, storage)
    row = await _settings_row(db)
    assert row.signature_in_letter is True, "sanity: the kind default really is on"

    result = await resolve_signature_data_uri(db, document="letter", override=False)
    assert result is None


@pytest.mark.asyncio
async def test_override_none_falls_back_to_cv_kind_default(db, storage, monkeypatch):
    monkeypatch.setattr("applire.storage.get_storage", lambda: storage)
    from applire.services.signature import resolve_signature_data_uri

    await _upload(db, storage)
    # Default is off, override None: must stay off (unchanged #359 behaviour).
    assert await resolve_signature_data_uri(db, document="cv", override=None) is None

    await _set_kind_default(db, cv=True)
    # Flip the kind default on; override None must now follow it.
    result = await resolve_signature_data_uri(db, document="cv", override=None)
    assert result is not None


@pytest.mark.asyncio
async def test_override_none_falls_back_to_letter_kind_default(db, storage, monkeypatch):
    monkeypatch.setattr("applire.storage.get_storage", lambda: storage)
    from applire.services.signature import resolve_signature_data_uri

    await _upload(db, storage)
    assert await resolve_signature_data_uri(db, document="letter", override=None) is not None

    await _set_kind_default(db, letter=False)
    assert await resolve_signature_data_uri(db, document="letter", override=None) is None


@pytest.mark.asyncio
async def test_resolve_signature_bytes_honors_override(db, storage, monkeypatch):
    monkeypatch.setattr("applire.storage.get_storage", lambda: storage)
    from applire.services.signature import resolve_signature_bytes

    png = _real_png_bytes()
    await _upload(db, storage, file_bytes=png)
    # CV kind default off; override True must still yield bytes.
    assert await resolve_signature_bytes(db, document="cv", override=True) == png
    # override False must suppress it even though letter default is on.
    assert await resolve_signature_bytes(db, document="letter", override=False) is None


@pytest.mark.asyncio
async def test_override_with_no_settings_row_stays_none_and_never_touches_storage(db):
    """No signature ever uploaded: even override=True cannot conjure a path
    that was never stored — storage must never even be consulted."""
    from applire.services.signature import resolve_signature_data_uri

    assert await _settings_row(db) is None

    class _Unreachable:
        async def read(self, path):
            raise AssertionError("storage.read() must not be called")

    import applire.storage as storage_module

    original = storage_module.get_storage
    storage_module.get_storage = lambda: _Unreachable()
    try:
        result = await resolve_signature_data_uri(db, document="cv", override=True)
    finally:
        storage_module.get_storage = original
    assert result is None


# ---------------------------------------------------------------------------
# 3. resolve_signature_effective / resolve_signature_available
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signature_effective_matches_the_render_precedence(db, storage, monkeypatch):
    monkeypatch.setattr("applire.storage.get_storage", lambda: storage)
    from applire.services.signature import resolve_signature_effective

    await _upload(db, storage)
    # cv kind default off, override None -> not effective
    assert await resolve_signature_effective(db, document="cv", override=None) is False
    # override True -> effective regardless of kind default
    assert await resolve_signature_effective(db, document="cv", override=True) is True
    # letter kind default on, override False -> not effective
    assert await resolve_signature_effective(db, document="letter", override=False) is False


@pytest.mark.asyncio
async def test_signature_available_false_before_upload_true_after(db, storage, monkeypatch):
    """Independent of any toggle or override — the question the control needs
    to decide whether to render itself at all vs. a disabled hint."""
    monkeypatch.setattr("applire.storage.get_storage", lambda: storage)
    from applire.services.signature import resolve_signature_available

    assert await resolve_signature_available(db) is False

    await _upload(db, storage)
    assert await resolve_signature_available(db) is True

    # Even with the kind default off and no override, availability stays True
    # — it is not the same question as "would this document show it".
    row = await _settings_row(db)
    assert row.signature_in_cv is False
    assert await resolve_signature_available(db) is True


@pytest.mark.asyncio
async def test_signature_available_false_after_delete(db, storage, monkeypatch):
    monkeypatch.setattr("applire.storage.get_storage", lambda: storage)
    from applire.services.signature import delete_signature, resolve_signature_available

    user_id = uuid.uuid4()
    from applire.models.user import User

    db.add(User(id=user_id, email=f"{user_id}@example.de"))
    await db.commit()
    await _upload(db, storage)
    assert await resolve_signature_available(db) is True

    await delete_signature(user_id=user_id, db=db, storage=storage)
    assert await resolve_signature_available(db) is False


# ---------------------------------------------------------------------------
# 4. One seam test per render seam (four), each proving override wins in
#    BOTH directions and None falls back to the kind default.
# ---------------------------------------------------------------------------


_CASES = [
    # (kind_default, override, expect_signature)
    pytest.param(True, False, False, id="override-off-wins-over-kind-default-on"),
    pytest.param(False, True, True, id="override-on-wins-over-kind-default-off"),
    pytest.param(True, None, True, id="none-falls-back-to-kind-default-on"),
    pytest.param(False, None, False, id="none-falls-back-to-kind-default-off"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind_default,override,expect_signature", _CASES)
async def test_seam_cv_html(db, storage, monkeypatch, kind_default, override, expect_signature):
    """Seam 1/4: get_cv_html (the PDF/HTML render path)."""
    monkeypatch.setattr("applire.storage.get_storage", lambda: storage)
    from applire.services.cv import get_cv_html

    await _upload(db, storage)
    await _set_kind_default(db, cv=kind_default)
    job_id, profile_id = uuid.uuid4(), uuid.uuid4()
    cv = _ready_cv(job_id, profile_id, signature_override=override)
    db.add(cv)
    await db.commit()

    html = await get_cv_html(cv.id, db)
    assert ("data:image/png;base64," in html) is expect_signature


@pytest.mark.asyncio
@pytest.mark.parametrize("kind_default,override,expect_signature", _CASES)
async def test_seam_cv_docx(db, storage, monkeypatch, kind_default, override, expect_signature):
    """Seam 2/4: get_cv_docx (_prepare_cv_docx_render + render_cv_docx)."""
    monkeypatch.setattr("applire.storage.get_storage", lambda: storage)
    from docx import Document as open_docx

    from applire.services.cv import get_cv_docx

    await _upload(db, storage)
    await _set_kind_default(db, cv=kind_default)
    job_id, profile_id = uuid.uuid4(), uuid.uuid4()
    cv = _ready_cv(job_id, profile_id, signature_override=override)
    db.add(cv)
    await db.commit()

    docx_bytes = await get_cv_docx(cv.id, db)
    document = open_docx(io.BytesIO(docx_bytes))
    inline_shape_count = len(document.inline_shapes)
    assert (inline_shape_count == 1) is expect_signature, (
        f"expected {'a' if expect_signature else 'no'} signature inline shape, "
        f"got {inline_shape_count}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind_default,override,expect_signature", _CASES)
async def test_seam_cover_letter_html(db, storage, monkeypatch, kind_default, override, expect_signature):
    """Seam 3/4: get_cover_letter_html (the PDF/HTML render path)."""
    monkeypatch.setattr("applire.storage.get_storage", lambda: storage)
    from applire.services.cover_letter import get_cover_letter_html

    await _upload(db, storage)
    await _set_kind_default(db, letter=kind_default)
    job_id, profile_id = uuid.uuid4(), uuid.uuid4()
    letter = _ready_letter(job_id, profile_id, signature_override=override)
    db.add(letter)
    await db.commit()

    html = await get_cover_letter_html(letter.id, db)
    assert ("data:image/png;base64," in html) is expect_signature


@pytest.mark.asyncio
@pytest.mark.parametrize("kind_default,override,expect_signature", _CASES)
async def test_seam_cover_letter_docx(db, storage, monkeypatch, kind_default, override, expect_signature):
    """Seam 4/4: get_cover_letter_docx (_prepare_cover_letter_docx_render +
    render_letter_docx)."""
    monkeypatch.setattr("applire.storage.get_storage", lambda: storage)
    from docx import Document as open_docx

    from applire.services.cover_letter import get_cover_letter_docx

    await _upload(db, storage)
    await _set_kind_default(db, letter=kind_default)
    job_id, profile_id = uuid.uuid4(), uuid.uuid4()
    letter = _ready_letter(job_id, profile_id, signature_override=override)
    db.add(letter)
    await db.commit()

    docx_bytes = await get_cover_letter_docx(letter.id, db)
    document = open_docx(io.BytesIO(docx_bytes))
    inline_shape_count = len(document.inline_shapes)
    assert (inline_shape_count == 1) is expect_signature, (
        f"expected {'a' if expect_signature else 'no'} signature inline shape, "
        f"got {inline_shape_count}"
    )


# ---------------------------------------------------------------------------
# 5. set_cv_signature_override / set_cover_letter_signature_override
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_cv_signature_override_persists_and_returns_effective(db, storage, monkeypatch):
    monkeypatch.setattr("applire.storage.get_storage", lambda: storage)
    from applire.services.cv import set_cv_signature_override

    await _upload(db, storage)  # cv kind default off
    job_id, profile_id = uuid.uuid4(), uuid.uuid4()
    cv = _ready_cv(job_id, profile_id, signature_override=None)
    db.add(cv)
    await db.commit()

    effective = await set_cv_signature_override(cv.id, True, db)
    assert effective is True
    await db.refresh(cv)
    assert cv.signature_override is True

    effective = await set_cv_signature_override(cv.id, None, db)
    assert effective is False, "reset to the kind default (off)"
    await db.refresh(cv)
    assert cv.signature_override is None


@pytest.mark.asyncio
async def test_set_cv_signature_override_raises_lookuperror_for_unknown_cv(db):
    from applire.services.cv import set_cv_signature_override

    with pytest.raises(LookupError):
        await set_cv_signature_override(uuid.uuid4(), True, db)


@pytest.mark.asyncio
async def test_set_cv_signature_override_raises_lookuperror_for_not_ready_cv(db):
    from applire.models.cv import GeneratedCV
    from applire.services.cv import set_cv_signature_override

    cv = GeneratedCV(
        id=uuid.uuid4(), job_analysis_id=uuid.uuid4(), profile_id=uuid.uuid4(),
        tailored_data={}, template="classic_german", status="pending",
    )
    db.add(cv)
    await db.commit()

    with pytest.raises(LookupError):
        await set_cv_signature_override(cv.id, True, db)


@pytest.mark.asyncio
async def test_set_cover_letter_signature_override_persists_and_returns_effective(db, storage, monkeypatch):
    monkeypatch.setattr("applire.storage.get_storage", lambda: storage)
    from applire.services.cover_letter import set_cover_letter_signature_override

    await _upload(db, storage)  # letter kind default on
    job_id, profile_id = uuid.uuid4(), uuid.uuid4()
    letter = _ready_letter(job_id, profile_id, signature_override=None)
    db.add(letter)
    await db.commit()

    effective = await set_cover_letter_signature_override(letter.id, False, db)
    assert effective is False
    await db.refresh(letter)
    assert letter.signature_override is False

    effective = await set_cover_letter_signature_override(letter.id, None, db)
    assert effective is True, "reset to the kind default (on)"
    await db.refresh(letter)
    assert letter.signature_override is None


@pytest.mark.asyncio
async def test_set_cover_letter_signature_override_raises_lookuperror_for_unknown_letter(db):
    from applire.services.cover_letter import set_cover_letter_signature_override

    with pytest.raises(LookupError):
        await set_cover_letter_signature_override(uuid.uuid4(), True, db)


@pytest.mark.asyncio
async def test_set_cover_letter_signature_override_raises_lookuperror_for_not_ready_letter(db):
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.services.cover_letter import set_cover_letter_signature_override

    letter = GeneratedCoverLetter(
        id=uuid.uuid4(), job_analysis_id=uuid.uuid4(), profile_id=uuid.uuid4(),
        template="classic_german", letter_data={}, status="pending",
    )
    db.add(letter)
    await db.commit()

    with pytest.raises(LookupError):
        await set_cover_letter_signature_override(letter.id, True, db)


# ---------------------------------------------------------------------------
# get_cv_status / get_cover_letter_status expose the three fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_cv_status_exposes_override_effective_and_available(db, storage, monkeypatch):
    monkeypatch.setattr("applire.storage.get_storage", lambda: storage)
    from applire.services.cv import get_cv_status

    await _upload(db, storage)
    job_id, profile_id = uuid.uuid4(), uuid.uuid4()
    cv = _ready_cv(job_id, profile_id, signature_override=True)
    db.add(cv)
    await db.commit()

    result = await get_cv_status(cv.id, db, "http://test")
    assert result.signature_override is True
    assert result.signature_effective is True
    assert result.signature_available is True


@pytest.mark.asyncio
async def test_get_cv_status_signature_available_false_with_no_upload(db):
    from applire.services.cv import get_cv_status

    job_id, profile_id = uuid.uuid4(), uuid.uuid4()
    cv = _ready_cv(job_id, profile_id)
    db.add(cv)
    await db.commit()

    result = await get_cv_status(cv.id, db, "http://test")
    assert result.signature_override is None
    assert result.signature_effective is False
    assert result.signature_available is False


@pytest.mark.asyncio
async def test_get_cover_letter_status_exposes_override_effective_and_available(db, storage, monkeypatch):
    monkeypatch.setattr("applire.storage.get_storage", lambda: storage)
    from applire.services.cover_letter import get_cover_letter_status

    await _upload(db, storage)
    job_id, profile_id = uuid.uuid4(), uuid.uuid4()
    letter = _ready_letter(job_id, profile_id, signature_override=False)
    db.add(letter)
    await db.commit()

    result = await get_cover_letter_status(letter.id, db, "http://test")
    assert result.signature_override is False
    assert result.signature_effective is False
    assert result.signature_available is True
