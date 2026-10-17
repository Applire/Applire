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

"""#781 (founder ruling F-2) — the profile photo's framing, as pure functions.

The stored photo is the unmodified upload; the candidate's framing
(`PersonalInfo.photo_crop`) is applied at render by :func:`frame_photo`, the ONE
helper `services.cv._resolve_photo_data_uri` passes every CV template's and the
Word export's photo through. :func:`crop_rect` is the twin of
`frontend/lib/photo-crop.ts`'s `cropRect`; a parity table in both test suites
pins that they agree. Upload validation (:func:`check_photo_image`) and the
render share one bounded, format-allow-listed open.

No user, no database: the owner-scoped reads and writes stay in
`services/photo.py`.
"""
import io
import logging

from applire.schemas.profile import PhotoCrop

logger = logging.getLogger(__name__)

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
