# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The admin sees metadata, never content (ADR-091 cl. 27, S-4, W0B-1).

Three proofs, each independent of reviewer attention:
1. every compiled metadata statement touches only allowlisted columns;
2. the admin routers import no content model and no content service;
3. the admin response models have exactly the agreed fields, so no content field
   can appear in a response.
"""

from __future__ import annotations

import ast
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql

from applire.models.application import Application
from applire.models.uploads import UploadRecord
from applire.schemas import admin as admin_schemas
from applire.services.admin import metadata
from tests.support.owners_1b import add_user

APPLIRE = Path(metadata.__file__).resolve().parents[2]
ALLOWED_TABLES = {"applications", "generated_cvs", "generated_cover_letters", "uploads",
                  "llm_usage", "master_profiles"}


def _columns(sql: str) -> set[tuple[str, str]]:
    return set(re.findall(r"\b([a-z_]+)\.([a-z_]+)\b", sql))


def _compiled(stmts) -> list[str]:
    return [str(s.compile(dialect=postgresql.dialect())) for s in stmts.values()]


def _assert_allowed(sqls):
    assert sqls, "no statements compiled"
    for sql in sqls:
        cols = _columns(sql)
        assert cols, sql
        for table, col in cols:
            assert table in ALLOWED_TABLES, (table, sql)
            assert col in metadata.ALLOWED_COLUMNS, (table, col, sql)


def test_compiled_sql_on_this_schema_uses_only_allowed_columns():
    _assert_allowed(_compiled(metadata.metadata_statements([uuid.uuid4()])))


def test_compiled_sql_with_every_owner_column_present_uses_only_allowed_columns(monkeypatch):
    """After 3a's 0074/0075 every model has an owner key; simulate both document paths."""
    monkeypatch.setattr(metadata, "_owner_col", lambda m: getattr(m, "user_id", m.id))
    stmts = metadata.metadata_statements([uuid.uuid4()])
    assert set(stmts) == {"application_count", "cv_count", "letter_count", "storage_bytes", "ai_tokens_30d"}
    _assert_allowed(_compiled(stmts))

    # The profile path: chain tables WITHOUT an owner column (the pre-0075 shape;
    # after the W1 merge the real models carry ``user_id``, so hide it explicitly).
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.models.cv import GeneratedCV
    from applire.models.profile import MasterProfile
    monkeypatch.setattr(
        metadata, "_owner_col",
        lambda m: m.id if m is MasterProfile
        else None if m in (GeneratedCV, GeneratedCoverLetter)
        else getattr(m, "user_id", None),
    )
    stmts = metadata.metadata_statements([uuid.uuid4()])
    assert "cv_count" in stmts and "JOIN master_profiles" in _compiled({"x": stmts["cv_count"]})[0]
    _assert_allowed(_compiled(stmts))


def test_allowlist_holds_no_content_column():
    for col in metadata.ALLOWED_COLUMNS:
        assert col in {"id", "user_id", "profile_id", "created_at", "deleted_at", "byte_size",
                       "total_tokens"}, col


# --- 2. imports -------------------------------------------------------------------

ROUTERS = ["routers/admin/users.py", "routers/me_account.py"]
ALLOWED_MODEL_MODULES = {"applire.models.user", "applire.models.auth", "applire.models.audit"}
ALLOWED_SERVICE_MODULES = {"applire.services", "applire.services.admin",
                           "applire.services.admin.metadata", "applire.services.admin.users",
                           "applire.services.admin.links", "applire.services.admin.identity_seams",
                           "applire.services.audit", "applire.services.mail",
                           "applire.services.erasure"}


def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            for alias in node.names:
                out.add(f"{node.module}.{alias.name}")
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out


@pytest.mark.parametrize("rel", ROUTERS)
def test_admin_routers_import_no_content_model_or_service(rel):
    mods = _imports(APPLIRE / rel)
    models = {m for m in mods if m.startswith("applire.models.")}
    # "applire.models.user.User" -> module part
    model_modules = {".".join(m.split(".")[:3]) for m in models}
    assert model_modules <= ALLOWED_MODEL_MODULES, model_modules - ALLOWED_MODEL_MODULES
    services = {m for m in mods if m.startswith("applire.services")}
    service_modules = {m for m in services
                       if m in ALLOWED_SERVICE_MODULES or ".".join(m.split(".")[:-1]) in ALLOWED_SERVICE_MODULES}
    assert services == service_modules, services - service_modules


def test_service_modules_behind_the_router_import_no_content_model():
    for rel in ("services/admin/users.py", "services/admin/links.py", "services/admin/identity_seams.py"):
        mods = {".".join(m.split(".")[:3]) for m in _imports(APPLIRE / rel) if m.startswith("applire.models.")}
        assert mods <= ALLOWED_MODEL_MODULES | {"applire.models.user_settings"}, (rel, mods)


# --- 3. response shapes -----------------------------------------------------------

def test_admin_user_item_has_exactly_the_agreed_fields():
    assert set(admin_schemas.AdminUserItem.model_fields) == {
        "id", "email", "role", "status", "created_at", "last_login_at", "last_active_at",
        "invite_expires_at", "metadata"}
    assert set(admin_schemas.AdminUserMetadata.model_fields) == {
        "application_count", "document_count", "storage_bytes", "ai_tokens_30d"}
    for model in (admin_schemas.AdminUserItem, admin_schemas.AdminUserMetadata,
                  admin_schemas.IssuedLink, admin_schemas.AdminUserCreatedResponse):
        assert model.model_config.get("extra") != "allow", model


# --- collect() counts the right person's rows -------------------------------------

@pytest.mark.asyncio
async def test_collect_counts_per_person(async_db):
    a = await add_user(async_db)
    b = await add_user(async_db)
    now = datetime.now(timezone.utc)
    for _ in range(2):
        async_db.add(Application(user_id=a.id, job_analysis_id=uuid.uuid4()))
    async_db.add(Application(user_id=b.id, job_analysis_id=uuid.uuid4()))
    for owner, size in ((a.id, 1000), (a.id, 500), (b.id, 7)):
        async_db.add(UploadRecord(user_id=owner, original_filename="cv.pdf", content_hash="h",
                                  mime_type="application/pdf", file_path="/x", byte_size=size,
                                  expires_at=now + timedelta(days=1)))
    await async_db.commit()
    out = await metadata.collect(async_db, [a.id, b.id])
    assert (out[a.id].application_count, out[a.id].storage_bytes) == (2, 1500)
    assert (out[b.id].application_count, out[b.id].storage_bytes) == (1, 7)
    # llm_usage.user_id arrives with 3a's 0075: until then the usage cell is unknown, not 0.
    from applire.models.llm_usage import LlmUsage
    expected = None if not hasattr(LlmUsage, "user_id") else 0
    assert out[a.id].ai_tokens_30d == expected


@pytest.mark.asyncio
async def test_users_list_response_carries_metadata_only(async_db):
    from tests.support.owners_1b import build_app, client_for
    app, acting = build_app(async_db)
    admin = await add_user(async_db, role="admin")
    acting.user_id = admin.id
    async with client_for(app) as client:
        r = await client.get("/api/admin/users")
    item = r.json()["users"][0]
    assert set(item) == set(admin_schemas.AdminUserItem.model_fields)
    assert set(item["metadata"]) == set(admin_schemas.AdminUserMetadata.model_fields)
