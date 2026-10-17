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
#: The largest photo (in pixels) the service decodes. 40 MP keeps every phone's
#: default output (12–24 MP) and most cameras' full resolution; a decode of the
#: bound costs ~120 MB RGB once per render (adversarial pass 2026-10-17: a
#: 160 KB 49 MP PNG cost ~580 MB RSS at the earlier 50 MP bound). JPEGs are
#: additionally decoded at a reduced scale (`draft`) — they never need more
#: pixels than the rendered size. Anything larger is refused on upload.
MAX_PHOTO_PIXELS = 40_000_000
MAX_PHOTO_SIDE = 12_000
#: The longest side of an UNFRAMED photo as rendered. Re-encoded like a framed
#: one (metadata dropped), never upscaled; ~900 dpi on the largest photo box.
UNFRAMED_MAX_SIDE = 1600
#: EXIF orientations that swap width and height.
_SWAPPING_ORIENTATIONS = {5, 6, 7, 8}


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
    """The photo as it is shown: ``raw`` framed by ``crop``, same format,
    re-encoded WITHOUT metadata.

    ``crop`` None is the identity framing: the whole image (templates crop it
    `object-fit: cover; center top`, exactly as before #781), downscaled to
    :data:`UNFRAMED_MAX_SIDE` and re-encoded. Re-encoding is unconditional
    because the upload is stored as the candidate sent it (ruling F-2) — with
    its EXIF (camera, time, GPS) — and a delivered PDF/DOCX must carry the
    picture, not the file's metadata (adversarial pass 2026-10-17). Only the
    ICC colour profile is kept.

    The EXIF orientation is applied first, because the browser adjuster
    measured the image as the browser displays it. An image Pillow cannot read,
    of another format than declared, or beyond the pixel bound is returned
    unchanged — the render must never lose the photo, or crash, over its
    framing. (Uploads are checked on the way in, so that fallback is only
    reachable for a file stored before the check existed.)
    """
    try:
        from PIL import Image, ImageOps

        with _open_checked(raw, content_type) as opened:
            w0, h0 = opened.size
            try:
                orientation = opened.getexif().get(0x0112)
            except Exception:  # noqa: BLE001
                orientation = None
            # The displayed (EXIF-oriented) size, which the geometry is relative to.
            dw, dh = (h0, w0) if orientation in _SWAPPING_ORIENTATIONS else (w0, h0)
            if crop is not None:
                _, _, sw, sh = crop_rect(dw, dh, crop)
                out_w = max(1, round(min(FRAMED_MAX_WIDTH, sw)))
                ratio = out_w / sw
            else:
                ratio = min(1.0, UNFRAMED_MAX_SIDE / max(dw, dh))
            if opened.format == "JPEG" and ratio < 1.0:
                # Decode at the smallest DCT scale that still covers the output.
                opened.draft("RGB", (max(1, int(w0 * ratio) + 1), max(1, int(h0 * ratio) + 1)))
            icc = opened.info.get("icc_profile")
            img = ImageOps.exif_transpose(opened)
            if crop is not None:
                sx, sy, sw, sh = crop_rect(img.width, img.height, crop)
                box = (round(sx), round(sy), round(sx + sw), round(sy + sh))
                out = img.crop(box).resize(
                    (out_w, max(1, round(out_w / PHOTO_ASPECT))), Image.Resampling.LANCZOS
                )
            else:
                scale = min(1.0, UNFRAMED_MAX_SIDE / max(img.width, img.height))
                size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
                out = img if size == img.size else img.resize(size, Image.Resampling.LANCZOS)
            fmt = _PIL_FORMAT.get(content_type, "JPEG")
            if fmt == "JPEG" and out.mode not in ("RGB", "L"):
                out = out.convert("RGB")
            # A fresh image carries no `info`; nothing but the pixels and the
            # colour profile is written (no EXIF, XMP, PNG text chunks).
            clean = Image.new(out.mode, out.size)
            clean.paste(out)
            if out.mode == "P" and out.getpalette():
                clean.putpalette(out.getpalette())
            kwargs: dict = {"quality": 90} if fmt in ("JPEG", "WEBP") else {}
            if icc:
                kwargs["icc_profile"] = icc
            buf = io.BytesIO()
            clean.save(buf, format=fmt, **kwargs)
            return buf.getvalue()
    except Exception:  # noqa: BLE001 — never lose the photo over its framing
        logger.warning("photo re-encode failed; rendering the stored file", exc_info=True)
        return raw
