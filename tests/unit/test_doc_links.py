# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Signed document links (US328; ADR-091 cl. 18, D-5, RD-8; CONTRACT-CHANGE 1c-2).

``?exp=<unix>&uid=<user>&sig=<b64url>``, ``sig = HMAC(doc-link key,
"<kind>:<doc_id>:<user_id>:<link_epoch>:<exp>")``. Asserted here: the MAC binds
every field; ``link_epoch`` revokes outstanding links; ``exp`` past/non-numeric →
410; an inactive or non-owning signer gets nothing; the dependency never falls
back from a bearer to a link; document responses carry ``Referrer-Policy`` +
``Cache-Control``; the access log never prints ``sig=`` or a token.
"""

from __future__ import annotations

import logging
import time
import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import APIRouter, Depends
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from applire.auth import links
from applire.auth.deps_links import (
    DOCUMENT_PATH_RE,
    DocumentResponseHeaders,
    user_or_signed_link,
)
from applire.auth.logfilter import (
    REDACTED,
    AccessLogRedactionFilter,
    install_access_log_redaction,
    redact,
)
from applire.models.user import User
from tests.support.owners_1c import (  # noqa: F401 (fixture)
    TEST_SECRET,
    build_app,
    make_document,
    make_person,
    token_db,
)

BASE = "http://applire.test/api/cv/{id}/pdf"


def _params(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


@pytest.fixture
def secret():
    links.set_instance_secret(TEST_SECRET)
    yield
    links.set_instance_secret(None)


def _user(epoch: int = 0):
    return SimpleNamespace(id=uuid.uuid4(), link_epoch=epoch)


# ── pure signing ────────────────────────────────────────────────────────────


def test_sign_appends_exp_uid_sig_and_keeps_the_existing_query(secret):
    doc = uuid.uuid4()
    user = _user()
    url = links.sign_document_url("cv", doc, user, BASE.format(id=doc) + "?download=1", now=1_000)
    params = _params(url)
    assert params["download"] == "1"
    assert params["uid"] == str(user.id)
    assert int(params["exp"]) == 1_000 + 3600  # RD-8: 60 minutes
    assert len(params["sig"]) == 43  # sha256, unpadded b64url


def test_resigning_replaces_rather_than_duplicates(secret):
    doc = uuid.uuid4()
    user = _user()
    once = links.sign_document_url("cv", doc, user, BASE.format(id=doc), now=1_000)
    twice = links.sign_document_url("cv", doc, user, once, now=2_000)
    q = parse_qs(urlsplit(twice).query)
    assert len(q["sig"]) == len(q["exp"]) == len(q["uid"]) == 1
    assert q["exp"] == [str(2_000 + 3600)]


def test_ttl_follows_the_setting(secret, monkeypatch):
    monkeypatch.setattr(links, "_configured_ttl_minutes", lambda: 5)
    doc = uuid.uuid4()
    url = links.sign_document_url("cv", doc, _user(), BASE.format(id=doc), now=0)
    assert _params(url)["exp"] == "300"


def test_the_mac_binds_kind_doc_user_epoch_and_exp(secret):
    doc, uid = uuid.uuid4(), uuid.uuid4()
    base = links._mac("cv", doc, uid, 0, "100")
    assert links._mac("cover_letter", doc, uid, 0, "100") != base
    assert links._mac("cv", uuid.uuid4(), uid, 0, "100") != base
    assert links._mac("cv", doc, uuid.uuid4(), 0, "100") != base
    assert links._mac("cv", doc, uid, 1, "100") != base
    assert links._mac("cv", doc, uid, 0, "101") != base


def test_keys_are_purpose_separated(secret):
    assert links.derive_key("doc-link") != links.derive_key("oidc-state")
    with pytest.raises(ValueError):
        links.derive_key("anything-else")  # type: ignore[arg-type]


def test_a_different_instance_secret_gives_a_different_signature(secret):
    doc, uid = uuid.uuid4(), uuid.uuid4()
    first = links._mac("cv", doc, uid, 0, "100")
    links.set_instance_secret("another-secret")
    assert links._mac("cv", doc, uid, 0, "100") != first


def test_signing_without_a_secret_refuses():
    links.set_instance_secret(None)
    with pytest.raises(links.InstanceSecretMissing):
        links.sign_document_url("cv", uuid.uuid4(), _user(), "http://x/api/cv/1/pdf")


@pytest.mark.parametrize("exp", ["", "abc", "12.5", "-5", "１２３", "9" * 20])
def test_non_numeric_exp_is_expired(exp):
    with pytest.raises(links.LinkExpired):
        links.check_exp(exp, now=0)


def test_past_and_present_exp_are_expired_future_is_not():
    with pytest.raises(links.LinkExpired):
        links.check_exp("99", now=100)
    with pytest.raises(links.LinkExpired):
        links.check_exp("100", now=100)
    assert links.check_exp("101", now=100) == 101


# ── verification against the DB ─────────────────────────────────────────────


async def _signed(db, kind="cv", *, owner=None, now=None):
    owner = owner or await make_person(db)
    doc = await make_document(db, kind, owner)
    path = "cv" if kind == "cv" else "cover-letter"
    url = links.sign_document_url(kind, doc.id, owner, f"http://t/api/{path}/{doc.id}/html", now=now)
    return owner, doc, _params(url)


@pytest.mark.asyncio
async def test_a_valid_link_resolves_its_signer(token_db):
    async with token_db.session() as db:
        owner, doc, p = await _signed(db)
        user = await links.verify_document_link("cv", doc.id, p["exp"], p["sig"], db, p["uid"])
        assert user is not None and user.id == owner.id


@pytest.mark.asyncio
async def test_verification_lazy_loads_the_secret_from_instance_state(token_db):
    from datetime import datetime, timezone

    from applire.models.instance_state import InstanceState

    async with token_db.session() as db:
        owner, doc, p = await _signed(db)
        db.add(InstanceState(key=links.INSTANCE_SECRET_KEY, value=TEST_SECRET,
                             updated_at=datetime.now(timezone.utc)))
        await db.commit()
        links.set_instance_secret(None)
        user = await links.verify_document_link("cv", doc.id, p["exp"], p["sig"], db, p["uid"])
        assert user is not None and user.id == owner.id


@pytest.mark.asyncio
async def test_no_secret_anywhere_means_no_link_is_valid(token_db):
    async with token_db.session() as db:
        _owner, doc, p = await _signed(db)
        links.set_instance_secret(None)
        assert await links.verify_document_link("cv", doc.id, p["exp"], p["sig"], db, p["uid"]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["sig", "uid", "doc", "kind", "exp"])
async def test_tampering_any_field_is_refused(token_db, field):
    async with token_db.session() as db:
        owner, doc, p = await _signed(db)
        other = await make_person(db)
        kind, doc_id = "cv", doc.id
        if field == "sig":
            p["sig"] = ("A" if p["sig"][0] != "A" else "B") + p["sig"][1:]
        elif field == "uid":
            p["uid"] = str(other.id)
        elif field == "doc":
            doc_id = (await make_document(db, "cv", owner)).id
        elif field == "kind":
            kind = "cover_letter"
        elif field == "exp":
            p["exp"] = str(int(p["exp"]) + 60)
        assert await links.verify_document_link(kind, doc_id, p["exp"], p["sig"], db, p["uid"]) is None


@pytest.mark.asyncio
async def test_bumping_link_epoch_kills_outstanding_links(token_db):
    """adversarial-security §S: the link dies with the token (revoke/disable/delete)."""
    async with token_db.session() as db:
        owner, doc, p = await _signed(db)
        await db.execute(update(User).where(User.id == owner.id).values(link_epoch=User.link_epoch + 1))
        await db.commit()
        assert await links.verify_document_link("cv", doc.id, p["exp"], p["sig"], db, p["uid"]) is None


@pytest.mark.asyncio
async def test_an_expired_link_raises_link_expired(token_db):
    async with token_db.session() as db:
        _owner, doc, p = await _signed(db, now=time.time() - 3 * 3600)
        with pytest.raises(links.LinkExpired):
            await links.verify_document_link("cv", doc.id, p["exp"], p["sig"], db, p["uid"])


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["disabled", "deleted"])
async def test_an_inactive_signer_gets_nothing(token_db, state):
    from datetime import datetime, timezone

    async with token_db.session() as db:
        owner, doc, p = await _signed(db)
        col = User.disabled_at if state == "disabled" else User.deleted_at
        await db.execute(update(User).where(User.id == owner.id).values({col: datetime.now(timezone.utc)}))
        await db.commit()
        assert await links.verify_document_link("cv", doc.id, p["exp"], p["sig"], db, p["uid"]) is None


@pytest.mark.asyncio
async def test_a_link_for_someone_elses_document_is_refused(token_db):
    """A correctly-MACed link whose signer does not own the document (only
    constructible with the secret) still opens nothing — the ownership check."""
    if not hasattr(__import__("applire.models.cv", fromlist=["GeneratedCV"]).GeneratedCV, "user_id"):
        pytest.skip("owner column arrives with 3a's migration 0075 (integration)")
    async with token_db.session() as db:
        owner = await make_person(db)
        stranger = await make_person(db)
        doc = await make_document(db, "cv", owner)
        url = links.sign_document_url("cv", doc.id, stranger, f"http://t/api/cv/{doc.id}/pdf")
        p = _params(url)
        assert await links.verify_document_link("cv", doc.id, p["exp"], p["sig"], db, p["uid"]) is None


@pytest.mark.asyncio
async def test_a_missing_document_is_refused(token_db):
    async with token_db.session() as db:
        owner = await make_person(db)
        ghost = uuid.uuid4()
        p = _params(links.sign_document_url("cv", ghost, owner, f"http://t/api/cv/{ghost}/pdf"))
        assert await links.verify_document_link("cv", ghost, p["exp"], p["sig"], db, p["uid"]) is None


# ── the dependency over HTTP ─────────────────────────────────────────────────

_doc_router = APIRouter()


@_doc_router.get("/api/cv/{cv_id}/pdf")
async def _cv_pdf(cv_id: uuid.UUID, user: User = Depends(user_or_signed_link)):
    from fastapi import Response

    from applire.ownership import current_owner

    owner = current_owner()
    return Response(
        content=f"{user.id}|{owner.user_id if owner else None}", media_type="application/pdf"
    )


@_doc_router.get("/api/cover-letter/{cl_id}/html")
async def _cl_html(cl_id: uuid.UUID, user: User = Depends(user_or_signed_link)):
    return {"user": str(user.id)}


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
@pytest.mark.no_owner_context
async def test_signed_link_authenticates_without_a_session_and_sets_the_owner(token_db):
    async with token_db.session() as db:
        owner, doc, p = await _signed(db)
    app = build_app(token_db, [_doc_router], as_user=None, middleware=[DocumentResponseHeaders])
    async with _client(app) as c:
        r = await c.get(f"/api/cv/{doc.id}/pdf", params=p)
    assert r.status_code == 200, r.text
    assert r.text == f"{owner.id}|{owner.id}"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_cover_letter_links_work_too(token_db):
    async with token_db.session() as db:
        owner, doc, p = await _signed(db, "cover_letter")
    app = build_app(token_db, [_doc_router], as_user=None)
    async with _client(app) as c:
        r = await c.get(f"/api/cover-letter/{doc.id}/html", params=p)
    assert r.status_code == 200 and r.json() == {"user": str(owner.id)}


@pytest.mark.asyncio
async def test_no_session_and_no_link_is_401(token_db):
    app = build_app(token_db, [_doc_router], as_user=None)
    async with _client(app) as c:
        r = await c.get(f"/api/cv/{uuid.uuid4()}/pdf")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_an_invalid_link_without_a_session_is_401(token_db):
    async with token_db.session() as db:
        _owner, doc, p = await _signed(db)
    p["sig"] = "x" * 43
    app = build_app(token_db, [_doc_router], as_user=None)
    async with _client(app) as c:
        r = await c.get(f"/api/cv/{doc.id}/pdf", params=p)
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_an_expired_link_without_a_session_is_410(token_db):
    async with token_db.session() as db:
        _owner, doc, p = await _signed(db, now=time.time() - 7200)
    app = build_app(token_db, [_doc_router], as_user=None)
    async with _client(app) as c:
        r = await c.get(f"/api/cv/{doc.id}/pdf", params=p)
    assert r.status_code == 410
    assert r.json()["detail"]["error_code"] == "link_expired"


@pytest.mark.asyncio
async def test_a_non_numeric_exp_is_410(token_db):
    async with token_db.session() as db:
        _owner, doc, p = await _signed(db)
    p["exp"] = "soon"
    app = build_app(token_db, [_doc_router], as_user=None)
    async with _client(app) as c:
        r = await c.get(f"/api/cv/{doc.id}/pdf", params=p)
    assert r.status_code == 410


@pytest.mark.asyncio
async def test_a_signed_in_user_is_served_without_a_link(token_db):
    async with token_db.session() as db:
        me = await make_person(db)
    app = build_app(token_db, [_doc_router], as_user=me)
    async with _client(app) as c:
        r = await c.get(f"/api/cv/{uuid.uuid4()}/pdf")
    assert r.status_code == 200 and r.text.startswith(str(me.id))


@pytest.mark.asyncio
async def test_a_bearer_header_disables_the_link_branch(token_db):
    """ADR-091 cl. 12: with an Authorization header only the bearer counts — a
    valid link beside an invalid bearer does not authenticate."""
    async with token_db.session() as db:
        _owner, doc, p = await _signed(db)
    app = build_app(token_db, [_doc_router], as_user=None)
    async with _client(app) as c:
        r = await c.get(f"/api/cv/{doc.id}/pdf", params=p, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_headers_are_replaced_not_duplicated_and_other_paths_untouched():
    from fastapi import FastAPI, Response

    app = FastAPI()

    @app.get("/api/cv/{cv_id}/html")
    async def _h(cv_id: str):
        return Response("x", headers={"Cache-Control": "public, max-age=600"})

    @app.get("/api/cv/{cv_id}")
    async def _meta(cv_id: str):
        return {"ok": True}

    app.add_middleware(DocumentResponseHeaders)
    async with _client(app) as c:
        doc = await c.get("/api/cv/abc/html")
        meta = await c.get("/api/cv/abc")
    assert doc.headers.get_list("cache-control") == ["private, no-store"]
    assert "referrer-policy" not in meta.headers


@pytest.mark.parametrize(
    "path,hit",
    [
        ("/api/cv/1/html", True), ("/api/cv/1/pdf", True), ("/api/cv/1/docx", True),
        ("/api/cover-letter/1/pdf", True), ("/api/cv/1", False), ("/api/cv/1/ats", False),
    ],
)
def test_document_path_set(path, hit):
    assert bool(DOCUMENT_PATH_RE.match(path)) is hit


# ── access-log redaction ─────────────────────────────────────────────────────

TOKEN = "apl_abcd1234_" + "A" * 43


@pytest.mark.parametrize(
    "line,secret",
    [
        ("/api/cv/1/pdf?exp=1&uid=u&sig=SECRETSIG", "SECRETSIG"),
        ("/api/cv/1/pdf?sig=SECRETSIG&exp=1", "SECRETSIG"),
        ("/api/auth/links/SECRETLINKTOKEN", "SECRETLINKTOKEN"),
        ("/x?token=SECRETTOK", "SECRETTOK"),
        ("/x?setup_token=SECRETSETUP", "SECRETSETUP"),
        (f"/x?foo={TOKEN}", TOKEN),
    ],
)
def test_redact_removes_secrets(line, secret):
    out = redact(line)
    assert secret not in out and REDACTED in out


def test_redact_keeps_exp_and_uid():
    out = redact("/api/cv/1/pdf?exp=123&uid=abc&sig=S")
    assert "exp=123" in out and "uid=abc" in out


def test_uvicorn_access_record_is_redacted_when_formatted():
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("10.0.0.1:5", "GET", "/api/cv/1/pdf?exp=1&uid=u&sig=SECRETSIG", "1.1", 200), None,
    )
    assert AccessLogRedactionFilter().filter(record) is True
    assert "SECRETSIG" not in record.getMessage()


def test_install_is_idempotent_and_filters_the_real_logger(caplog):
    logger = logging.getLogger("uvicorn.access")
    before = list(logger.filters)
    try:
        first = install_access_log_redaction()
        assert install_access_log_redaction() is first
        assert sum(isinstance(f, AccessLogRedactionFilter) for f in logger.filters) == 1
        with caplog.at_level(logging.INFO, logger="uvicorn.access"):
            logger.info('%s - "%s %s HTTP/%s" %d', "c", "GET", "/api/cv/1/pdf?sig=LEAKME", "1.1", 200)
        assert "LEAKME" not in caplog.text
    finally:
        logger.filters[:] = before
