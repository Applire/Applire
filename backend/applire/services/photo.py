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

"""Photo service — profile photo upload / delete / read (ADR-021).

upload_photo:  Validate, store, update personal_info.photo_url, record GDPR consent.
delete_photo:  Remove stored file, clear photo_url and consent.
get_photo:     Return raw bytes for the current user photo (GDPR portability).
set_photo_crop / frame_photo / photo_crop_for: the photo's framing (#781,
               founder ruling F-2) — geometry in the profile JSON, applied at
               render by ONE helper every CV template and the DOCX export read
               their photo bytes through (`services.cv._resolve_photo_data_uri`).
"""
import io
import logging
import mimetypes
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.profile import MasterProfile
from applire.models.user import User
from applire.schemas.profile import MasterProfileData, PhotoCrop
from applire.storage.base import StorageProvider

logger = logging.getLogger(__name__)

_ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_EXT_MAP = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


async def _get_user(user_id: uuid.UUID, db: AsyncSession) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise LookupError(f"User {user_id} not found")
    return user


async def _get_profile(db: AsyncSession, user_id: uuid.UUID) -> MasterProfile:
    """The photo owner's live profile (ADR-092 cl. 2) — never another user's."""
    from applire.services.profile import get_profile_for_user

    profile = await get_profile_for_user(db, user_id)
    if profile is None:
        raise LookupError("No profile found — import a CV first")
    return profile


async def upload_photo(
    *,
    user_id: uuid.UUID,
    file_bytes: bytes,
    content_type: str,
    db: AsyncSession,
    storage: StorageProvider,
    crop: PhotoCrop | None = None,
) -> dict[str, str]:
    """Validate, store, and record GDPR consent for a profile photo.

    Returns ``{"photo_url": str, "consent_at": str}`` on success.
    Raises ``ValueError`` for invalid format or size.

    #781 (ruling F-2): the upload is stored unmodified. ``crop`` is the framing
    the candidate chose in the adjuster for THIS file; a framing belongs to one
    image, so an upload without one clears whatever framing the previous photo
    had rather than applying it to a different picture.
    """
    if content_type not in _ALLOWED_CONTENT_TYPES:
        raise ValueError(
            f"Unsupported format '{content_type}'. Upload a JPEG, PNG, or WebP image."
        )
    if len(file_bytes) > _MAX_BYTES:
        raise ValueError("Photo exceeds the 5 MB limit. Please use a smaller file.")
    # The bytes must BE the declared kind of image, within the pixel bound —
    # every render decodes this file (#781 framing), so it is checked once here.
    check_photo_image(file_bytes, content_type)

    user = await _get_user(user_id, db)
    profile = await _get_profile(db, user_id)

    profile_data = MasterProfileData.model_validate(profile.profile_json)
    old_path = profile_data.personal_info.photo_url

    # Save new file first — if this fails, the old file is still intact
    ext = _EXT_MAP[content_type]
    path = await storage.save(file_bytes, f"photo{ext}")

    # Delete old file after the new one is safely stored (best-effort; if this
    # fails, the retention worker's orphan scan reclaims the old file within
    # 24h once nothing references it — issue #152)
    if old_path:
        try:
            await storage.delete(old_path)
        except Exception:
            pass

    # Persist photo_url in master profile JSONB
    updated_pi = profile_data.personal_info.model_copy(
        update={"photo_url": path, "photo_crop": crop}
    )
    profile_data = profile_data.model_copy(update={"personal_info": updated_pi})
    profile.profile_json = profile_data.model_dump(mode="json")

    # Record GDPR consent
    now = datetime.now(timezone.utc)
    user.photo_consent = True
    user.photo_consent_at = now

    await db.commit()
    return {"photo_url": path, "consent_at": now.isoformat()}


async def delete_photo(
    *,
    user_id: uuid.UUID,
    db: AsyncSession,
    storage: StorageProvider,
) -> None:
    """Remove stored photo file and clear consent. No-op if no photo on file."""
    user = await _get_user(user_id, db)
    profile = await _get_profile(db, user_id)

    profile_data = MasterProfileData.model_validate(profile.profile_json)
    if profile_data.personal_info.photo_url:
        await storage.delete(profile_data.personal_info.photo_url)
        updated_pi = profile_data.personal_info.model_copy(
            update={"photo_url": None, "photo_crop": None}
        )
        profile.profile_json = profile_data.model_copy(
            update={"personal_info": updated_pi}
        ).model_dump(mode="json")

    user.photo_consent = False
    user.photo_consent_at = None
    await db.commit()


async def get_photo_bytes(
    *,
    user_id: uuid.UUID,
    db: AsyncSession,
    storage: StorageProvider,
) -> tuple[bytes, str]:
    """Return raw photo bytes and MIME type for the user. Raises LookupError if no photo."""
    await _get_user(user_id, db)
    profile = await _get_profile(db, user_id)
    profile_data = MasterProfileData.model_validate(profile.profile_json)
    path = profile_data.personal_info.photo_url
    if not path:
        raise LookupError("No profile photo on file")
    try:
        raw = await storage.read(path)
    except FileNotFoundError as exc:
        # A deleted file, or (MD-30) a path the provider refuses as outside its
        # storage: both are "no photo on file" (404), never a 500 or a read.
        raise LookupError("No profile photo on file") from exc
    content_type = mimetypes.guess_type(path)[0] or "image/jpeg"
    return raw, content_type


# ─── #781 — framing (founder ruling F-2, 2026-10-16) ──────────────────────────
#
# The geometry mirrors `frontend/lib/photo-crop.ts` exactly; the parity table in
# `tests/unit/test_photo_framing.py` and `lib/__tests__/photo-crop.test.ts` pins
# that both compute the same rectangle for the same input.

#: Width ÷ height of the framed photo. The templates' boxes are 1:1 circles
#: (executive, modern_swiss, creative_sidebar) and a 45×55 mm portrait
#: (lebenslauf), all `object-fit: cover; object-position: center top`: a 4:5
#: frame fills the portrait within 2 % and the circles show its top square.
PHOTO_ASPECT = 4 / 5
MAX_ZOOM = 4.0
#: The widest side of a framed photo; a smaller crop is never upscaled.
FRAMED_MAX_WIDTH = 800

_PIL_FORMAT = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
#: The largest photo (in pixels) the service decodes — a 48 MP phone camera
#: fits; anything larger is refused on upload and rendered unframed.
MAX_PHOTO_PIXELS = 50_000_000
MAX_PHOTO_SIDE = 12_000


def _open_checked(raw: bytes, content_type: str):
    """Open ``raw`` as exactly the declared format, refusing oversize images.

    Only the header is read here (Pillow decodes lazily), so the dimension
    check runs BEFORE any pixel is decoded. Raises ``ValueError`` with a
    candidate-facing message for anything that is not a JPEG/PNG/WebP of the
    declared type within the pixel bound.
    """
    from PIL import Image

    fmt = _PIL_FORMAT.get(content_type)
    if fmt is None:
        raise ValueError("Unsupported format. Upload a JPEG, PNG, or WebP image.")
    try:
        img = Image.open(io.BytesIO(raw), formats=[fmt])
    except Exception as exc:  # noqa: BLE001 — Pillow raises several types here
        raise ValueError(
            "The file is not a readable JPEG, PNG, or WebP image."
        ) from exc
    width, height = img.size
    if (
        width < 1
        or height < 1
        or width > MAX_PHOTO_SIDE
        or height > MAX_PHOTO_SIDE
        or width * height > MAX_PHOTO_PIXELS
    ):
        img.close()
        raise ValueError("The photo's dimensions are too large. Please use a smaller image.")
    return img


def check_photo_image(raw: bytes, content_type: str) -> None:
    """Upload-time check: the declared type, a readable image, within bounds."""
    img = _open_checked(raw, content_type)
    try:
        img.verify()
    except Exception as exc:  # noqa: BLE001
        raise ValueError("The file is not a readable JPEG, PNG, or WebP image.") from exc
    finally:
        img.close()


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(max(v, lo), hi)


def crop_rect(width: int, height: int, crop: PhotoCrop) -> tuple[float, float, float, float]:
    """The framing's rectangle ``(sx, sy, sw, sh)`` in source pixels.

    Zoom 1 is the largest 4:5 rectangle the image holds; the centre is clamped
    so the rectangle never leaves the image (a stored framing can therefore
    never render a strip outside the photo, whatever the JSON says).
    """
    zoom = _clamp(crop.zoom, 1.0, MAX_ZOOM)
    if width / height > PHOTO_ASPECT:
        sw, sh = height * PHOTO_ASPECT, float(height)
    else:
        sw, sh = float(width), width / PHOTO_ASPECT
    sw, sh = sw / zoom, sh / zoom
    cx = _clamp(crop.cx, sw / 2 / width, 1 - sw / 2 / width)
    cy = _clamp(crop.cy, sh / 2 / height, 1 - sh / 2 / height)
    return cx * width - sw / 2, cy * height - sh / 2, sw, sh


def frame_photo(raw: bytes, crop: PhotoCrop | None, content_type: str) -> bytes:
    """The photo as it is shown: ``raw`` framed by ``crop``, same format.

    ``crop`` None → ``raw`` unchanged (a photo nobody framed renders exactly as
    before #781). The EXIF orientation is applied first, because the browser
    adjuster measured the image as the browser displays it. An image Pillow
    cannot read, of another format than declared, or beyond the pixel bound
    is returned unchanged — the render must never lose the photo, or crash,
    over its framing.
    """
    if crop is None:
        return raw
    try:
        from PIL import Image, ImageOps

        with _open_checked(raw, content_type) as opened:
            img = ImageOps.exif_transpose(opened)
            sx, sy, sw, sh = crop_rect(img.width, img.height, crop)
            box = (round(sx), round(sy), round(sx + sw), round(sy + sh))
            framed = img.crop(box)
            out_w = max(1, round(min(FRAMED_MAX_WIDTH, sw)))
            out_h = max(1, round(out_w / PHOTO_ASPECT))
            framed = framed.resize((out_w, out_h), Image.Resampling.LANCZOS)
            fmt = _PIL_FORMAT.get(content_type, "JPEG")
            if fmt == "JPEG" and framed.mode not in ("RGB", "L"):
                framed = framed.convert("RGB")
            buf = io.BytesIO()
            framed.save(buf, format=fmt, **({"quality": 90} if fmt in ("JPEG", "WEBP") else {}))
            return buf.getvalue()
    except Exception:  # noqa: BLE001 — never lose the photo over its framing
        logger.warning("photo framing failed; rendering the photo unframed", exc_info=True)
        return raw


async def photo_crop_for(
    db: AsyncSession, user_id: uuid.UUID | None, photo_path: str | None
) -> PhotoCrop | None:
    """The owner's framing — but only for the photo it was chosen for.

    A document stores the photo path it was generated with; once a newer photo
    replaced it, that path is no longer ``personal_info.photo_url`` and the
    current framing (chosen for the NEW image) must not be applied to it.
    """
    if not photo_path or user_id is None:
        return None
    from applire.services.profile import get_profile_for_user

    profile = await get_profile_for_user(db, user_id)
    if profile is None:
        return None
    pi = (profile.profile_json or {}).get("personal_info") or {}
    if pi.get("photo_url") != photo_path or not pi.get("photo_crop"):
        return None
    try:
        return PhotoCrop.model_validate(pi["photo_crop"])
    except ValueError:
        return None


async def set_photo_crop(
    *,
    user_id: uuid.UUID,
    crop: PhotoCrop | None,
    db: AsyncSession,
) -> dict:
    """Store (or, with None, clear) the framing of the photo on file.

    Raises ``LookupError`` when there is no photo to frame. Every document that
    shows this photo picks the new framing up at its next render.
    """
    profile = await _get_profile(db, user_id)
    profile_data = MasterProfileData.model_validate(profile.profile_json)
    if not profile_data.personal_info.photo_url:
        raise LookupError("No profile photo on file")
    updated_pi = profile_data.personal_info.model_copy(update={"photo_crop": crop})
    profile.profile_json = profile_data.model_copy(
        update={"personal_info": updated_pi}
    ).model_dump(mode="json")
    await db.commit()
    return {"photo_crop": crop.model_dump(mode="json") if crop else None}
