# Copyright (C) 2026 Tobias Rosenbaum
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

"""#781 (founder ruling F-2) — the profile photo is framed by GEOMETRY.

The stored file stays the unmodified upload; ``PersonalInfo.photo_crop`` holds
``{cx, cy, zoom}`` and ONE helper, ``services.photo.frame_photo``, applies it at
render inside ``services.cv._resolve_photo_data_uri`` — the seam every CV
template and the DOCX prep read their photo through. One named test per seam:

* the geometry twin (``crop_rect`` == ``frontend/lib/photo-crop.ts``'s ``cropRect``);
* ``frame_photo`` itself (4:5, format kept, EXIF first, fall back to the raw file);
* the upload bounds (declared type, header-checked pixel bound — nothing stored);
* upload / delete / set-crop semantics, service level AND over the real routes;
* the render seams (the four photo templates + the DOCX prep + a stale document);
* every control that must NOT see the framing (prompt view, gap relevance, the
  section door, import, undo) and every reader that MUST (dump, GDPR export,
  REST/MCP ``get_profile``).

No Docker, no browser, no LLM; images are synthetic, drawn with Pillow.
"""
from __future__ import annotations

import base64
import copy
import io
import re
import struct
import uuid
import zlib
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import applire.models  # noqa: F401
from applire import ownership
from applire.db.session import Base
from applire.models.profile import MasterProfile
from applire.models.user import User
from applire.schemas.profile import MasterProfileData, PersonalInfo, PhotoCrop
from applire.services import photo as photo_svc
from applire.services import photo_framing as framing
from applire.storage.local import LocalStorageProvider
from tests.support.profile_factory import make_master_profile, set_profile_json

pytestmark = pytest.mark.no_owner_context

CROP = PhotoCrop(cx=0.8, cy=0.3, zoom=2.0)
CROP_JSON = {"cx": 0.8, "cy": 0.3, "zoom": 2.0}
OTHER_CROP = PhotoCrop(cx=0.1, cy=0.9, zoom=3.0)


# ─── synthetic images ────────────────────────────────────────────────────────


def _encode(img: Image.Image, fmt: str, **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt, **kw)
    return buf.getvalue()


def _landscape_with_red_right_third(fmt: str = "JPEG") -> bytes:
    """1800x1012 (the #781 shape): blue everywhere, a solid red block in the right third."""
    img = Image.new("RGB", (1800, 1012), (20, 40, 200))
    img.paste((220, 20, 20), (1200, 0, 1800, 1012))
    return _encode(img, fmt, **({"quality": 95} if fmt == "JPEG" else {}))


def _is_red(px) -> bool:
    r, g, b = px[:3]
    return r > 180 and g < 80 and b < 80


def _is_blue(px) -> bool:
    r, g, b = px[:3]
    return b > 150 and r < 80 and g < 100


def _real_jpeg() -> bytes:
    return _encode(Image.new("RGB", (8, 10), (200, 180, 160)), "JPEG")


def _png_with_ihdr(width: int, height: int) -> bytes:
    """A tiny valid PNG whose IHDR *claims* width x height (CRC recomputed).

    Header-only forgery: nothing here ever allocates the claimed pixels, which is
    exactly the point — the bound must refuse it from the header.
    """
    raw = _encode(Image.new("RGB", (2, 2), (1, 2, 3)), "PNG")
    assert raw[12:16] == b"IHDR"
    ihdr = struct.pack(">II", width, height) + raw[24:29]
    crc = struct.pack(">I", zlib.crc32(b"IHDR" + ihdr) & 0xFFFFFFFF)
    return raw[:16] + ihdr + crc + raw[33:]


def _decode(raw: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(raw))
    img.load()
    return img


# ─── 1. the geometry twin of frontend/lib/photo-crop.ts ──────────────────────

#: [w, h, cx, cy, zoom] -> (sx, sy, sw, sh). The expected rectangles were worked
#: out BY HAND from the TypeScript algorithm (`cropSize` + `clampCrop` +
#: `cropRect`) and are pinned verbatim in `lib/__tests__/photo-crop.test.ts`
#: ("parity with services/photo.crop_rect"); never regenerate them from Python.
PARITY_TABLE = [
    ((1800, 1012, 0.5, 0.0, 1.0), (495.2, 0.0, 809.6, 1012.0)),
    ((1800, 1012, 0.0, 0.5, 1.0), (0.0, 0.0, 809.6, 1012.0)),  # cx=0 clamps left
    ((1800, 1012, 1.0, 0.5, 1.0), (990.4, 0.0, 809.6, 1012.0)),  # cx=1 clamps right
    ((1800, 1012, 0.8, 0.5, 2.0), (1237.6, 253.0, 404.8, 506.0)),
    ((1800, 1012, 0.8, 0.1, 4.0), (1338.8, 0.0, 202.4, 253.0)),  # zoom 4, cy clamps to the top
    ((480, 600, 0.3, 0.7, 1.0), (0.0, 0.0, 480.0, 600.0)),  # exactly 4:5
    ((600, 1000, 0.9, 0.2, 1.5), (200.0, 0.0, 400.0, 500.0)),  # portrait source
    ((600, 1000, 0.5, 0.0, 1.0), (0.0, 0.0, 600.0, 750.0)),
    ((1000, 1000, 0.5, 0.5, 1.0), (100.0, 0.0, 800.0, 1000.0)),
    ((1000, 1000, 0.5, 0.5, 3.0), (366.6666667, 333.3333333, 266.6666667, 333.3333333)),
    ((4000, 3000, 0.25, 0.25, 2.0), (400.0, 0.0, 1200.0, 1500.0)),
]


@pytest.mark.parametrize("args, expected", PARITY_TABLE)
def test_crop_rect_matches_the_frontend_twin(args, expected):
    w, h, cx, cy, zoom = args
    got = framing.crop_rect(w, h, PhotoCrop(cx=cx, cy=cy, zoom=zoom))
    assert got == pytest.approx(expected, abs=1e-5)


def test_crop_rect_never_leaves_the_image_for_any_stored_geometry():
    for w, h in ((1800, 1012), (480, 600), (600, 1000), (1000, 1000)):
        for cx in (0.0, 0.5, 1.0):
            for cy in (0.0, 0.5, 1.0):
                for zoom in (1.0, 2.5, 4.0):
                    sx, sy, sw, sh = framing.crop_rect(w, h, PhotoCrop(cx=cx, cy=cy, zoom=zoom))
                    assert sx >= -1e-6 and sy >= -1e-6
                    assert sx + sw <= w + 1e-6 and sy + sh <= h + 1e-6
                    assert sw / sh == pytest.approx(framing.PHOTO_ASPECT)


def test_photo_crop_schema_bounds_and_extras():
    with pytest.raises(ValueError):
        PhotoCrop(cx=0.5, cy=0.5, zoom=5.0)
    with pytest.raises(ValueError):
        PhotoCrop(cx=1.5, cy=0.5, zoom=1.0)
    with pytest.raises(ValueError):
        PhotoCrop(cx=0.5, cy=0.5, zoom=0.5)
    assert PhotoCrop.model_validate({**CROP_JSON, "junk": 1}) == CROP  # extra="ignore"


# ─── 2. frame_photo ──────────────────────────────────────────────────────────


def test_frame_photo_without_a_crop_returns_the_identical_bytes():
    raw = _landscape_with_red_right_third()
    assert framing.frame_photo(raw, None, "image/jpeg") is raw


def test_frame_photo_follows_the_crop_to_the_right_third_in_4_by_5():
    raw = _landscape_with_red_right_third()
    framed = framing.frame_photo(raw, PhotoCrop(cx=0.8, cy=0.5, zoom=1.0), "image/jpeg")
    out = _decode(framed)
    assert out.format == "JPEG"
    assert out.size == (800, 1000)  # 4:5, never upscaled past FRAMED_MAX_WIDTH
    assert out.width / out.height == pytest.approx(0.8)
    assert _is_red(out.getpixel((out.width // 2, out.height // 2)))
    # The unframed photo's centre is NOT in the red block — the crop moved it.
    original = _decode(raw)
    assert not _is_red(original.getpixel((original.width // 2, original.height // 2)))


def test_frame_photo_png_in_png_out_and_webp_in_webp_out():
    for fmt, mime in (("PNG", "image/png"), ("WEBP", "image/webp")):
        framed = framing.frame_photo(_landscape_with_red_right_third(fmt), CROP, mime)
        assert _decode(framed).format == fmt
        assert _decode(framed).width / _decode(framed).height == pytest.approx(0.8, abs=0.01)


def test_frame_photo_applies_exif_orientation_before_cropping():
    # Raw is 200x100: left half red, right half blue. Orientation 6 displays it
    # rotated 90 deg clockwise -> a 100x200 image, red on TOP, blue below.
    img = Image.new("RGB", (200, 100), (20, 40, 200))
    img.paste((220, 20, 20), (0, 0, 100, 100))
    exif = Image.Exif()
    exif[0x0112] = 6
    raw = _encode(img, "JPEG", quality=95, exif=exif)
    framed = _decode(framing.frame_photo(raw, PhotoCrop(cx=0.5, cy=0.0, zoom=1.0), "image/jpeg"))
    # Displayed 100x200 -> 4:5 crop is 100x125 (the raw 200x100 would give 80x100).
    assert framed.size == (100, 125)
    assert _is_red(framed.getpixel((50, 10)))
    assert _is_blue(framed.getpixel((50, 120)))


def test_frame_photo_returns_raw_when_the_bytes_are_not_the_declared_format():
    png_bytes = _landscape_with_red_right_third("PNG")
    assert framing.frame_photo(png_bytes, CROP, "image/jpeg") is png_bytes
    jpeg_bytes = _landscape_with_red_right_third("JPEG")
    assert framing.frame_photo(jpeg_bytes, CROP, "image/png") is jpeg_bytes
    garbage = b"not an image at all"
    assert framing.frame_photo(garbage, CROP, "image/jpeg") is garbage


@pytest.mark.parametrize("w, h", [(20000, 20000), (13000, 10)])
def test_frame_photo_returns_raw_for_a_forged_oversize_header(w, h):
    forged = _png_with_ihdr(w, h)
    assert framing.frame_photo(forged, CROP, "image/png") is forged


def test_frame_photo_refuses_a_real_image_beyond_the_side_bound():
    """A REAL 13000x10 PNG decodes fine in Pillow — only the bound refuses it."""
    wide = _encode(Image.new("RGB", (framing.MAX_PHOTO_SIDE + 1, 10), (9, 9, 9)), "PNG")
    assert framing.frame_photo(wide, CROP, "image/png") is wide


def test_frame_photo_refuses_a_real_image_beyond_the_pixel_bound():
    """12000 x 4200 = 50.4 MP: each side within bound, the product is not."""
    big = _encode(Image.new("L", (12000, 4200), 7), "PNG")
    assert 12000 <= framing.MAX_PHOTO_SIDE
    assert 12000 * 4200 > framing.MAX_PHOTO_PIXELS
    assert framing.frame_photo(big, CROP, "image/png") is big


# ─── a two-user world ────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def world():
    eng = create_async_engine("sqlite+aiosqlite://")
    factory = async_sessionmaker(eng, expire_on_commit=False)
    with ownership.unscoped("tooling"):
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as s:
            a = User(id=uuid.uuid4(), email="framing-a@example.org", role="user")
            s.add(a)
            await s.commit()
    yield factory, a
    await eng.dispose()


async def _seed_profile(factory, user, photo_url=None, crop=None, **pi):
    with ownership.unscoped("tooling"):
        async with factory() as s:
            p = make_master_profile(
                user_id=user.id,
                profile_json=MasterProfileData(
                    personal_info=PersonalInfo(name="Alex Brandt", photo_url=photo_url, photo_crop=crop, **pi)
                ).model_dump(mode="json"),
            )
            s.add(p)
            await s.commit()
            return p.id


async def _personal_info(factory, user) -> dict:
    with ownership.owner_context(user.id):
        async with factory() as s:
            p = (await s.execute(select(MasterProfile).where(MasterProfile.user_id == user.id))).scalar_one()
            return dict(p.profile_json["personal_info"])


@pytest.fixture
def upload_dir(tmp_path):
    d = tmp_path / "uploads"
    d.mkdir()
    return d


# ─── 3. upload refusals (security) ───────────────────────────────────────────


def _gif() -> bytes:
    return _encode(Image.new("P", (8, 8), 1), "GIF")


def _tiff() -> bytes:
    return _encode(Image.new("RGB", (8, 8), (1, 2, 3)), "TIFF")


_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8"/></svg>'

_REFUSED_UPLOADS = [
    pytest.param(_gif, "image/jpeg", id="gif-declared-jpeg"),
    pytest.param(_tiff, "image/png", id="tiff-declared-png"),
    pytest.param(lambda: _SVG, "image/webp", id="svg-declared-webp"),
    pytest.param(lambda: _png_with_ihdr(20000, 20000), "image/png", id="forged-ihdr-20000x20000"),
    pytest.param(lambda: _png_with_ihdr(13000, 10), "image/png", id="forged-ihdr-wider-than-the-side-bound"),
    pytest.param(lambda: _encode(Image.new("L", (12000, 4200), 7), "PNG"), "image/png", id="real-50MP-png"),
    pytest.param(lambda: _landscape_with_red_right_third("PNG"), "image/jpeg", id="png-declared-jpeg"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("make, declared", _REFUSED_UPLOADS)
async def test_upload_photo_refuses_a_file_that_is_not_the_declared_image_and_stores_nothing(
    world, upload_dir, make, declared
):
    factory, a = world
    await _seed_profile(factory, a, photo_url=None)
    with ownership.owner_context(a.id):
        async with factory() as s:
            with pytest.raises(ValueError):
                await photo_svc.upload_photo(
                    user_id=a.id, file_bytes=make(), content_type=declared, db=s,
                    storage=LocalStorageProvider(str(upload_dir)), crop=CROP,
                )
    assert list(upload_dir.iterdir()) == []
    pi = await _personal_info(factory, a)
    assert pi.get("photo_url") is None and "photo_crop" not in pi
    with ownership.unscoped("tooling"):
        async with factory() as s:
            assert (await s.get(User, a.id)).photo_consent is False


@pytest.mark.asyncio
async def test_a_refused_replacement_leaves_the_existing_photo_and_framing_standing(world, upload_dir):
    factory, a = world
    storage = LocalStorageProvider(str(upload_dir))
    stored = await storage.save(_real_jpeg(), "photo.jpg")
    await _seed_profile(factory, a, photo_url=stored, crop=CROP)
    with ownership.owner_context(a.id):
        async with factory() as s:
            with pytest.raises(ValueError):
                await photo_svc.upload_photo(
                    user_id=a.id, file_bytes=_gif(), content_type="image/jpeg", db=s, storage=storage,
                )
    pi = await _personal_info(factory, a)
    assert pi["photo_url"] == stored and pi["photo_crop"] == CROP_JSON
    assert await storage.read(stored) == _real_jpeg()


# ─── 4. upload / delete / set-crop semantics (service level) ─────────────────


@pytest.mark.asyncio
async def test_upload_with_a_crop_stores_it_and_a_second_upload_without_resets_it(world, upload_dir):
    factory, a = world
    storage = LocalStorageProvider(str(upload_dir))
    await _seed_profile(factory, a)
    with ownership.owner_context(a.id):
        async with factory() as s:
            first = await photo_svc.upload_photo(
                user_id=a.id, file_bytes=_real_jpeg(), content_type="image/jpeg", db=s,
                storage=storage, crop=CROP,
            )
        pi = await _personal_info(factory, a)
        assert pi["photo_url"] == first["photo_url"] and pi["photo_crop"] == CROP_JSON
        async with factory() as s:
            second = await photo_svc.upload_photo(
                user_id=a.id, file_bytes=_real_jpeg(), content_type="image/jpeg", db=s, storage=storage,
            )
    pi = await _personal_info(factory, a)
    assert pi["photo_url"] == second["photo_url"] != first["photo_url"]
    assert "photo_crop" not in pi  # reset: a framing belongs to ONE image


@pytest.mark.asyncio
async def test_delete_photo_clears_the_framing_with_the_photo(world, upload_dir):
    factory, a = world
    storage = LocalStorageProvider(str(upload_dir))
    stored = await storage.save(_real_jpeg(), "photo.jpg")
    await _seed_profile(factory, a, photo_url=stored, crop=CROP)
    with ownership.owner_context(a.id):
        async with factory() as s:
            await photo_svc.delete_photo(user_id=a.id, db=s, storage=storage)
    pi = await _personal_info(factory, a)
    assert pi.get("photo_url") is None and "photo_crop" not in pi


@pytest.mark.asyncio
async def test_set_photo_crop_stores_replaces_clears_and_needs_a_photo(world, upload_dir):
    factory, a = world
    storage = LocalStorageProvider(str(upload_dir))
    await _seed_profile(factory, a)  # no photo yet
    with ownership.owner_context(a.id):
        async with factory() as s:
            with pytest.raises(LookupError):
                await photo_svc.set_photo_crop(user_id=a.id, crop=CROP, db=s)
        async with factory() as s:
            up = await photo_svc.upload_photo(
                user_id=a.id, file_bytes=_real_jpeg(), content_type="image/jpeg", db=s, storage=storage,
            )
        async with factory() as s:
            assert await photo_svc.set_photo_crop(user_id=a.id, crop=CROP, db=s) == {"photo_crop": CROP_JSON}
        assert (await _personal_info(factory, a))["photo_crop"] == CROP_JSON
        async with factory() as s:
            await photo_svc.set_photo_crop(user_id=a.id, crop=OTHER_CROP, db=s)
        assert (await _personal_info(factory, a))["photo_crop"] == OTHER_CROP.model_dump()
        async with factory() as s:
            await photo_svc.set_photo_crop(user_id=a.id, crop=None, db=s)
    pi = await _personal_info(factory, a)
    assert "photo_crop" not in pi and pi["photo_url"] == up["photo_url"]  # the file is untouched


# ─── 4b. the same, over the real routes (full app, real auth override) ───────


async def _call(factory, user, storage, method, path, **kw):
    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.main import app
    from applire.routers.profile import _get_storage

    async def _db():
        async with factory() as s:
            yield s

    class _As:
        async def get_current_user(self, request, db=None):  # noqa: ANN001
            return user

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_auth_provider] = lambda: _As()
    app.dependency_overrides[_get_storage] = lambda: storage
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            return await c.request(method, path, **kw)
    finally:
        for dep in (get_db, get_auth_provider, _get_storage):
            app.dependency_overrides.pop(dep, None)


def _file(raw: bytes, mime: str = "image/jpeg"):
    return {"file": ("me.jpg", raw, mime)}


@pytest.mark.asyncio
async def test_route_post_photo_with_cx_cy_zoom_stores_the_framing(world, upload_dir):
    factory, a = world
    storage = LocalStorageProvider(str(upload_dir))
    await _seed_profile(factory, a)
    r = await _call(
        factory, a, storage, "POST", "/api/profile/photo?consent=true&cx=0.8&cy=0.3&zoom=2",
        files=_file(_real_jpeg()),
    )
    assert r.status_code == 200, r.text
    assert (await _personal_info(factory, a))["photo_crop"] == CROP_JSON
    # …and a later upload without framing resets it.
    r = await _call(factory, a, storage, "POST", "/api/profile/photo?consent=true", files=_file(_real_jpeg()))
    assert r.status_code == 200, r.text
    assert "photo_crop" not in await _personal_info(factory, a)


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["cx=0.5", "cx=0.5&cy=0.5", "zoom=2", "cy=0.2&zoom=2"])
async def test_route_post_photo_with_only_part_of_the_framing_is_422_and_stores_nothing(world, upload_dir, query):
    factory, a = world
    await _seed_profile(factory, a)
    r = await _call(
        factory, a, LocalStorageProvider(str(upload_dir)), "POST",
        f"/api/profile/photo?consent=true&{query}", files=_file(_real_jpeg()),
    )
    assert r.status_code == 422
    assert list(upload_dir.iterdir()) == []
    assert (await _personal_info(factory, a)).get("photo_url") is None


@pytest.mark.asyncio
async def test_route_post_photo_maps_the_declared_type_refusal_to_400(world, upload_dir):
    factory, a = world
    await _seed_profile(factory, a)
    r = await _call(
        factory, a, LocalStorageProvider(str(upload_dir)), "POST",
        "/api/profile/photo?consent=true", files=_file(_gif(), "image/jpeg"),
    )
    assert r.status_code == 400
    assert list(upload_dir.iterdir()) == []
    r = await _call(
        factory, a, LocalStorageProvider(str(upload_dir)), "POST",
        "/api/profile/photo?consent=true", files=_file(_png_with_ihdr(20000, 20000), "image/png"),
    )
    assert r.status_code == 400
    assert list(upload_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_route_put_photo_crop_is_404_without_a_photo_and_stores_with_one(world, upload_dir):
    factory, a = world
    storage = LocalStorageProvider(str(upload_dir))
    await _seed_profile(factory, a)
    r = await _call(factory, a, storage, "PUT", "/api/profile/photo/crop", json=CROP_JSON)
    assert r.status_code == 404
    up = await _call(factory, a, storage, "POST", "/api/profile/photo?consent=true", files=_file(_real_jpeg()))
    assert up.status_code == 200, up.text
    r = await _call(factory, a, storage, "PUT", "/api/profile/photo/crop", json=CROP_JSON)
    assert r.status_code == 200 and r.json() == {"photo_crop": CROP_JSON}
    assert (await _personal_info(factory, a))["photo_crop"] == CROP_JSON
    bad = await _call(factory, a, storage, "PUT", "/api/profile/photo/crop", json={**CROP_JSON, "zoom": 9})
    assert bad.status_code == 422


# ─── 5. render seams ─────────────────────────────────────────────────────────

_DATA_IMG = re.compile(r'<img[^>]*\bsrc="data:(image/[a-z+]+);base64,([A-Za-z0-9+/=]+)"')


def _embedded_photos(html: str) -> list[tuple[str, bytes]]:
    return [(m.group(1), base64.b64decode(m.group(2))) for m in _DATA_IMG.finditer(html)]


async def _seed_ready_cv(factory, user, *, template, document_photo, profile_photo, crop):
    """A ready CV that shows the photo at ``document_photo``; the owner's vault
    currently has ``profile_photo`` framed by ``crop``."""
    from applire.models.cv import GeneratedCV

    profile_id = await _seed_profile(factory, user, photo_url=profile_photo, crop=crop)
    with ownership.unscoped("tooling"):
        async with factory() as s:
            cv = GeneratedCV(
                job_analysis_id=uuid.uuid4(),
                profile_id=profile_id,
                user_id=user.id,
                template=template,
                tailored_data={
                    "contact": {"name": "Alex Brandt", "photo_url": document_photo},
                    "show_photo": True,
                },
                status="ready",
                document_language="de",
            )
            s.add(cv)
            await s.commit()
            return cv.id


async def _render(factory, user, storage, cv_id) -> str:
    from applire.services.cv import get_cv_html

    with ownership.owner_context(user.id), patch("applire.storage.get_storage", return_value=storage):
        async with factory() as s:
            return await get_cv_html(cv_id, s, user_id=user.id)


async def _assert_template_embeds_the_framed_photo(world, upload_dir, template):
    factory, a = world
    storage = LocalStorageProvider(str(upload_dir))
    raw = _landscape_with_red_right_third()
    path = await storage.save(raw, "photo.jpg")
    cv_id = await _seed_ready_cv(
        factory, a, template=template, document_photo=path, profile_photo=path, crop=CROP
    )
    html = await _render(factory, a, storage, cv_id)
    photos = _embedded_photos(html)
    assert len(photos) == 1, f"{template}: expected exactly one embedded photo"
    mime, payload = photos[0]
    assert mime == "image/jpeg"
    assert payload == framing.frame_photo(raw, CROP, "image/jpeg")
    assert payload != raw
    assert _decode(payload).size == (405, 506)  # zoom 2: 404.8 x 506 source px, 4:5


async def _assert_template_embeds_the_raw_photo_without_a_crop(world, upload_dir, template):
    factory, a = world
    storage = LocalStorageProvider(str(upload_dir))
    raw = _landscape_with_red_right_third()
    path = await storage.save(raw, "photo.jpg")
    cv_id = await _seed_ready_cv(
        factory, a, template=template, document_photo=path, profile_photo=path, crop=None
    )
    photos = _embedded_photos(await _render(factory, a, storage, cv_id))
    assert [p for _, p in photos] == [raw]


@pytest.mark.asyncio
async def test_render_seam_executive_embeds_the_framed_photo(world, upload_dir):
    await _assert_template_embeds_the_framed_photo(world, upload_dir, "executive")


@pytest.mark.asyncio
async def test_render_seam_modern_swiss_embeds_the_framed_photo(world, upload_dir):
    await _assert_template_embeds_the_framed_photo(world, upload_dir, "modern_swiss")


@pytest.mark.asyncio
async def test_render_seam_creative_sidebar_embeds_the_framed_photo(world, upload_dir):
    await _assert_template_embeds_the_framed_photo(world, upload_dir, "creative_sidebar")


@pytest.mark.asyncio
async def test_render_seam_lebenslauf_portrait_embeds_the_framed_photo(world, upload_dir):
    await _assert_template_embeds_the_framed_photo(world, upload_dir, "classic_german")


@pytest.mark.asyncio
@pytest.mark.parametrize("template", ["executive", "modern_swiss", "creative_sidebar", "classic_german"])
async def test_render_seam_without_a_crop_embeds_the_stored_bytes_unchanged(world, upload_dir, template):
    await _assert_template_embeds_the_raw_photo_without_a_crop(world, upload_dir, template)


def test_exactly_the_four_known_templates_render_a_photo():
    """A fifth photo-bearing template must get its own seam test above."""
    from applire.services.cv import _TEMPLATE_FILES, _TEMPLATES_DIR

    with_photo = {
        name for name, f in _TEMPLATE_FILES.items() if "photo_url" in (_TEMPLATES_DIR / f).read_text(encoding="utf-8")
    }
    assert with_photo == {"executive", "modern_swiss", "creative_sidebar", "classic_german"}


@pytest.mark.asyncio
async def test_render_seam_docx_prep_returns_the_framed_photo_bytes(world, upload_dir):
    from applire.models.cv import GeneratedCV
    from applire.services.cv import _prepare_cv_docx_render

    factory, a = world
    storage = LocalStorageProvider(str(upload_dir))
    raw = _landscape_with_red_right_third()
    path = await storage.save(raw, "photo.jpg")
    cv_id = await _seed_ready_cv(
        factory, a, template="classic_german", document_photo=path, profile_photo=path, crop=CROP
    )
    with ownership.owner_context(a.id), patch("applire.storage.get_storage", return_value=storage):
        async with factory() as s:
            record = await s.get(GeneratedCV, cv_id)
            photo_bytes = (await _prepare_cv_docx_render(record, s))[3]
    assert photo_bytes == framing.frame_photo(raw, CROP, "image/jpeg")
    assert photo_bytes != raw


@pytest.mark.asyncio
async def test_render_seam_docx_prep_without_a_crop_returns_the_stored_bytes(world, upload_dir):
    from applire.models.cv import GeneratedCV
    from applire.services.cv import _prepare_cv_docx_render

    factory, a = world
    storage = LocalStorageProvider(str(upload_dir))
    raw = _landscape_with_red_right_third()
    path = await storage.save(raw, "photo.jpg")
    cv_id = await _seed_ready_cv(
        factory, a, template="classic_german", document_photo=path, profile_photo=path, crop=None
    )
    with ownership.owner_context(a.id), patch("applire.storage.get_storage", return_value=storage):
        async with factory() as s:
            record = await s.get(GeneratedCV, cv_id)
            assert (await _prepare_cv_docx_render(record, s))[3] == raw


@pytest.mark.asyncio
async def test_resolve_photo_data_uri_keeps_a_png_a_png(upload_dir):
    from applire.services.cv import _resolve_photo_data_uri

    storage = LocalStorageProvider(str(upload_dir))
    raw = _landscape_with_red_right_third("PNG")
    path = await storage.save(raw, "photo.png")
    uri = await _resolve_photo_data_uri(path, storage, CROP)
    assert uri.startswith("data:image/png;base64,")
    payload = base64.b64decode(uri.partition(",")[2])
    assert _decode(payload).format == "PNG"
    assert payload == framing.frame_photo(raw, CROP, "image/png")


@pytest.mark.asyncio
async def test_photo_crop_for_applies_only_to_the_photo_it_was_chosen_for(world, upload_dir):
    factory, a = world
    storage = LocalStorageProvider(str(upload_dir))
    current = await storage.save(_real_jpeg(), "new.jpg")
    old = await storage.save(_real_jpeg(), "old.jpg")
    await _seed_profile(factory, a, photo_url=current, crop=CROP)
    with ownership.owner_context(a.id):
        async with factory() as s:
            assert await photo_svc.photo_crop_for(s, a.id, current) == CROP
            assert await photo_svc.photo_crop_for(s, a.id, old) is None  # a stale document
            assert await photo_svc.photo_crop_for(s, a.id, None) is None
            assert await photo_svc.photo_crop_for(s, None, current) is None


@pytest.mark.asyncio
async def test_photo_crop_for_is_none_for_a_photo_nobody_framed(world, upload_dir):
    factory, a = world
    storage = LocalStorageProvider(str(upload_dir))
    current = await storage.save(_real_jpeg(), "p.jpg")
    await _seed_profile(factory, a, photo_url=current, crop=None)
    with ownership.owner_context(a.id):
        async with factory() as s:
            assert await photo_svc.photo_crop_for(s, a.id, current) is None


@pytest.mark.asyncio
async def test_render_seam_a_stale_document_renders_its_own_photo_unframed(world, upload_dir):
    """The CV was generated with photo OLD; the vault has since moved to NEW
    (framed for NEW). OLD must render as stored, never under NEW's framing."""
    factory, a = world
    storage = LocalStorageProvider(str(upload_dir))
    old_raw = _landscape_with_red_right_third()
    old = await storage.save(old_raw, "old.jpg")
    new = await storage.save(_encode(Image.new("RGB", (900, 900), (0, 200, 0)), "JPEG"), "new.jpg")
    cv_id = await _seed_ready_cv(
        factory, a, template="executive", document_photo=old, profile_photo=new, crop=CROP
    )
    photos = _embedded_photos(await _render(factory, a, storage, cv_id))
    assert [p for _, p in photos] == [old_raw]


# ─── 6. exclusion seams ──────────────────────────────────────────────────────


def _vault_with_crop() -> dict:
    return MasterProfileData(
        personal_info=PersonalInfo(name="Alex Brandt", photo_url="/data/uploads/p.jpg", photo_crop=CROP)
    ).model_dump(mode="json")


def test_personal_info_dump_omits_photo_crop_while_unset_and_round_trips_it_when_set():
    unset = MasterProfileData(personal_info=PersonalInfo(name="A", photo_url="/p.jpg"))
    assert "photo_crop" not in unset.model_dump(mode="json")["personal_info"]
    assert "photo_crop" not in unset.personal_info.model_dump()
    dumped = MasterProfileData(
        personal_info=PersonalInfo(name="A", photo_url="/p.jpg", photo_crop=CROP)
    ).model_dump(mode="json")
    assert dumped["personal_info"]["photo_crop"] == CROP_JSON
    assert MasterProfileData.model_validate(dumped).personal_info.photo_crop == CROP
    # A profile persisted before the field existed still loads.
    legacy = {"personal_info": {"name": "A", "photo_url": "/p.jpg"}}
    assert MasterProfileData.model_validate(legacy).personal_info.photo_crop is None


def test_prompt_profile_view_strips_photo_crop_and_leaves_the_input_untouched():
    from applire.services.prompt_view import prompt_profile_view

    profile = _vault_with_crop()
    before = copy.deepcopy(profile)
    view = prompt_profile_view(profile)
    assert "photo_crop" not in view["personal_info"]
    assert view["personal_info"]["name"] == "Alex Brandt"  # nothing else touched
    assert profile == before and profile["personal_info"]["photo_crop"] == CROP_JSON


def test_gap_relevance_and_the_gap_input_hash_ignore_photo_crop():
    from applire.models.job import JobAnalysis
    from applire.services.gap import _gap_inputs_fingerprint, gap_relevant_profile

    framed, unframed = _vault_with_crop(), copy.deepcopy(_vault_with_crop())
    unframed["personal_info"].pop("photo_crop")
    assert gap_relevant_profile(framed) == gap_relevant_profile(unframed)

    job = JobAnalysis(
        role_title="Engineer", required_skills=[], nice_to_have_skills=[], keywords=[],
        seniority_level="senior", company_culture_signals=[], language_requirement="en",
    )
    assert _gap_inputs_fingerprint(job, make_master_profile(profile_json=framed)) == _gap_inputs_fingerprint(
        job, make_master_profile(profile_json=unframed)
    )


def test_section_door_refuses_photo_crop():
    from applire.services.profile.field_edit import build_replace_section_op

    with pytest.raises(ValueError, match="photo_crop"):
        build_replace_section_op("personal_info", {"name": "A", "photo_crop": CROP_JSON})
    with pytest.raises(ValueError, match="photo_crop"):
        build_replace_section_op("personal_info", {"photo_crop": None})


def test_import_keeps_the_stored_photo_crop_and_never_fills_an_empty_slot():
    from applire.services.profile.reconcile.apply import apply_ops
    from applire.services.profile.reconcile.ops import ApplyImportMerge

    def _pi(crop):
        return MasterProfileData(
            personal_info=PersonalInfo(name="M", photo_url="/data/uploads/mine.jpg", photo_crop=crop)
        )

    # A different framing arrives with the import: the stored one stands, no receipt.
    result = apply_ops(_pi(CROP), [ApplyImportMerge(merged=_pi(OTHER_CROP))], "cv_upload")
    assert result.profile.personal_info.photo_crop == CROP
    assert not [c for c in result.changes if c.field == "photo_crop"]
    # An import into an UNFRAMED vault does not write one either.
    result = apply_ops(_pi(None), [ApplyImportMerge(merged=_pi(OTHER_CROP))], "cv_upload")
    assert result.profile.personal_info.photo_crop is None
    # …nor does the very first import install one wholesale.
    result = apply_ops(MasterProfileData(), [ApplyImportMerge(merged=_pi(OTHER_CROP))], "cv_upload")
    assert result.profile.personal_info.photo_crop is None


def test_op_shaped_writers_cannot_write_photo_crop():
    """The reconcile model's `set_personal_info` and a `replace_section` op."""
    from applire.services.profile.reconcile.apply import apply_ops
    from applire.services.profile.reconcile.ops import ReplaceSection, SetPersonalInfo

    stored = MasterProfileData(
        personal_info=PersonalInfo(name="M", photo_url="/data/uploads/mine.jpg", photo_crop=CROP)
    )
    result = apply_ops(stored, [SetPersonalInfo(field="photo_crop", value=CROP_JSON | {"zoom": 4.0})], "cv_upload")
    assert result.profile.personal_info.photo_crop == CROP
    op = ReplaceSection(section="personal_info", value={"name": "M", "photo_crop": OTHER_CROP.model_dump()})
    result = apply_ops(stored, [op], "manual_edit")
    assert result.profile.personal_info.photo_crop == CROP


def test_undo_keeps_the_current_photo_crop_over_the_snapshots():
    from applire.services.profile.snapshots import _keep_user_managed_fields

    snapshot = {"personal_info": {"name": "Old", "photo_url": "/p.jpg", "photo_crop": OTHER_CROP.model_dump()}}
    current = {"personal_info": {"name": "New", "photo_url": "/p.jpg", "photo_crop": CROP_JSON}}
    out = _keep_user_managed_fields(snapshot, current)
    assert out["personal_info"]["photo_crop"] == CROP_JSON
    assert out["personal_info"]["name"] == "Old"  # the rest IS rolled back
    # The snapshot carried a framing the vault no longer has (photo replaced since).
    current_unframed = {"personal_info": {"name": "New", "photo_url": "/q.jpg"}}
    out = _keep_user_managed_fields(snapshot, current_unframed)
    assert "photo_crop" not in out["personal_info"] and out["personal_info"]["photo_url"] == "/q.jpg"


@pytest.mark.asyncio
async def test_undo_last_merge_keeps_the_current_photo_crop(world):
    from applire.services.profile.snapshots import capture_pre_merge_snapshot, undo_last_merge

    factory, a = world
    pid = await _seed_profile(factory, a, photo_url="/data/uploads/p.jpg", crop=OTHER_CROP)
    with ownership.owner_context(a.id):
        async with factory() as s:
            p = await s.get(MasterProfile, pid)
            await capture_pre_merge_snapshot(
                s, profile_id=pid, profile_json=dict(p.profile_json), enrichment_record_id=str(uuid.uuid4()),
            )
            new = dict(p.profile_json)
            new["personal_info"] = {**new["personal_info"], "photo_crop": CROP_JSON}
            set_profile_json(p, new)
            await s.commit()
        async with factory() as s:
            assert (await undo_last_merge(s, user_id=a.id)).restored
    assert (await _personal_info(factory, a))["photo_crop"] == CROP_JSON


@pytest.mark.asyncio
async def test_gdpr_export_and_rest_get_profile_carry_photo_crop(world, upload_dir):
    factory, a = world
    storage = LocalStorageProvider(str(upload_dir))
    await _seed_profile(factory, a, photo_url="/data/uploads/p.jpg", crop=CROP)
    export = await _call(factory, a, storage, "GET", "/api/profile/export")
    assert export.status_code == 200
    assert export.json()["profile"]["personal_info"]["photo_crop"] == CROP_JSON
    got = await _call(factory, a, storage, "GET", "/api/profile")
    assert got.status_code == 200
    assert got.json()["profile"]["personal_info"]["photo_crop"] == CROP_JSON


@pytest.mark.asyncio
async def test_mcp_get_profile_payload_carries_photo_crop():
    """The agent door: the real tool body over a real vault (the harness user's)."""
    from unittest.mock import AsyncMock, MagicMock

    from applire.auth.harness import STUB_USER_ID
    from applire.mcp.server import get_profile

    eng = create_async_engine("sqlite+aiosqlite://")
    factory = async_sessionmaker(eng, expire_on_commit=False)
    stub = User(id=STUB_USER_ID, email="local@applire.community", role="admin")
    with ownership.unscoped("tooling"):
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as s:
            s.add(stub)
            await s.commit()
    await _seed_profile(factory, stub, photo_url="/data/uploads/p.jpg", crop=CROP)
    try:
        async with factory() as s:
            cm = MagicMock()
            cm.__aenter__ = AsyncMock(return_value=s)
            cm.__aexit__ = AsyncMock(return_value=False)
            with patch("applire.mcp.server.get_db", return_value=cm):
                payload = await get_profile()
    finally:
        await eng.dispose()
    assert payload["profile"]["personal_info"]["photo_crop"] == CROP_JSON
