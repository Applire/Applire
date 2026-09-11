# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Signature service — the user's handwritten signature image (#359).

In DACH practice the Anschreiben is expected to carry a signature, and the
Lebenslauf traditionally ends with ``Ort, Datum`` and one. The workaround this
replaces is print -> sign -> scan, which loses the PDF's text layer and is
exactly the manual step the product exists to remove. Standard practice for a
digital application is an *inserted signature image*, not a scan of the whole
document.

**Storage, and why it is NOT the photo's.** The image goes through the
``StorageProvider`` and its path is kept on ``user_settings.signature_path``,
beside the two toggles that decide whether it renders — deliberately *not* in
``profile_json.personal_info`` where the photo's path lives (ADR-088, founder
ruling F-3 of 2026-09-11). Three reasons, in the order they were found:

1. The reconciler renders the whole ``personal_info`` object into the model's
   input view. ``photo_url`` is already in there; a file path is input an LLM
   has no business reading, and adding a second one makes that surface worse
   rather than equal.
2. That render is pinned byte-for-byte by the model-qualification goldens
   (``tests/files/model_matrix/golden/``), whose README states that editing them
   invalidates every published matrix row (#688).
3. A signature is document *chrome* the user attaches at render time, not a
   claim about the candidate. The photo is CV content and Art. 9 data; this is
   neither.

**The price, paid in code.** Leaving the vault means leaving the vault's file
lifecycle, so two mechanisms name this column explicitly rather than inheriting
it: the retention worker's orphan scan reads it into the referenced set (a
binary path absent from that set is not merely unprotected — its file is deleted
once past the grace period), and the Art. 17 erasure path deletes the file. It
also means this module writes **no vault state at all**, so ADR-063's named
write-door exception set stays exactly ``{commit, snapshots, photo}`` — three
modules, and the clause's "no fourth exception module" sentence stays true.

**No consent gate**, unlike ``services/photo.py``. A signature image is personal
data, and it is biometric *material*, but GDPR Art. 9 is a closed list and this
is not processed *for the purpose of uniquely identifying a natural person*
(Art. 4(14)): it is reproduced onto the user's own letter at their own
instruction. The absence of a gate is deliberate and recorded, because copying
``photo_consent`` blindly would have been the easy, wrong move and a later
reader would otherwise read the absence as an oversight.

**Rendering** is decided per document kind by two ``user_settings`` booleans
(``signature_in_letter`` default true, ``signature_in_cv`` default false —
founder default F-0). They are read at RENDER time rather than pinned onto the
document row, so a user can toggle and re-download without paying for a
regeneration; the cost of that trade (the state that decided a render is mutable
afterwards) is recorded in System-FMEA ``SF-PDF.6``.

**Per-document override (F-4b, founder ruling 2026-09-11).** On top of the
kind-level default, one specific generated CV or cover letter can carry its
own ``signature_override`` column (migration 0066, ``models/cv.GeneratedCV`` /
``models/cover_letter.GeneratedCoverLetter``): ``None`` = "use the kind
default" (unchanged behaviour), ``True``/``False`` = "with"/"without",
regardless of what the kind default says. Every resolution function below
takes an ``override`` parameter and the precedence is override-first —
``override if override is not None else <kind default>`` — evaluated in
``_signature_path_if_enabled``, the ONE seam every caller shares, so a
document's override cannot be honoured on the PDF/HTML path and ignored on
the DOCX path (same SF-PDF.6 discipline the kind toggle already has). Like
the kind toggle, it is read at RENDER time from the row a caller already
holds, not baked into a persisted artefact — toggling a document's override
and re-downloading takes effect without regenerating.
"""
from __future__ import annotations

import base64
import mimetypes
import uuid
from datetime import date
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.user import User
from applire.models.user_settings import UserSettings
from applire.storage.base import StorageProvider

DocumentKind = Literal["cv", "letter"]

# PNG first in the copy because a transparent PNG is what actually looks right
# on a rendered letter; JPEG/WebP are accepted because a phone photo of a
# signature is the realistic second input and refusing it would send the user
# back to a conversion tool. Background removal for photographed signatures is
# deliberately NOT in this build (founder default F-0) — it is a collector line.
_ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp"}
# 2 MB, not the photo's 5: a signature is a small monochrome image, and the cap
# is the cheapest honest defence against a user uploading a full scanned page.
_MAX_BYTES = 2 * 1024 * 1024
_EXT_MAP = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
_MIME_BY_SUFFIX = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}

_GERMAN_MONTHS = (
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)


async def _get_user(user_id: uuid.UUID, db: AsyncSession) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise LookupError(f"User {user_id} not found")
    return user


async def _get_settings_row(db: AsyncSession) -> UserSettings | None:
    """The CE single user's settings row, or None when none exists yet."""
    from applire.services.color_detection import _CE_STUB_USER_ID

    result = await db.execute(
        select(UserSettings).where(UserSettings.user_id == _CE_STUB_USER_ID)
    )
    return result.scalar_one_or_none()


async def _get_or_create_settings_row(db: AsyncSession) -> UserSettings:
    """The settings row, created if absent.

    A signature upload must not require a profile to exist first — unlike the
    photo, which patches a field of the Master Profile and therefore refuses
    with "import a CV first". Nothing about a signature depends on the vault
    having content, and refusing the upload would be an invented precondition.
    """
    from applire.services.color_detection import _CE_STUB_USER_ID

    row = await _get_settings_row(db)
    if row is None:
        row = UserSettings(user_id=_CE_STUB_USER_ID)
        db.add(row)
        await db.flush()
    return row


async def upload_signature(
    *,
    user_id: uuid.UUID,
    file_bytes: bytes,
    content_type: str,
    db: AsyncSession,
    storage: StorageProvider,
) -> dict[str, str]:
    """Validate and store a signature image; returns ``{"signature_url": path}``.

    Raises ``ValueError`` for an unsupported format or an oversize file, and
    ``LookupError`` when there is no user or no profile yet.
    """
    if content_type not in _ALLOWED_CONTENT_TYPES:
        raise ValueError(
            f"Unsupported format '{content_type}'. Upload a PNG, JPEG, or WebP image."
        )
    if not file_bytes:
        raise ValueError("The uploaded file is empty.")
    if len(file_bytes) > _MAX_BYTES:
        raise ValueError("Signature exceeds the 2 MB limit. Please use a smaller file.")

    await _get_user(user_id, db)
    row = await _get_or_create_settings_row(db)
    old_path = row.signature_path

    # Save the new file BEFORE deleting the old one: if the save fails, the user
    # still has the signature they had (services/photo.py's ordering, and its
    # reason).
    path = await storage.save(file_bytes, f"signature{_EXT_MAP[content_type]}")

    if old_path and old_path != path:
        try:
            await storage.delete(old_path)
        except Exception:
            # Best effort. The retention worker's orphan scan reclaims it within
            # 24 h once nothing references it (#152 / SF-PROFILE.5).
            pass

    row.signature_path = path
    await db.commit()
    return {"signature_url": path}


async def delete_signature(
    *,
    user_id: uuid.UUID,
    db: AsyncSession,
    storage: StorageProvider,
) -> None:
    """Remove the stored signature file and clear the path. No-op if none."""
    await _get_user(user_id, db)
    row = await _get_settings_row(db)
    if row is None or not row.signature_path:
        return
    try:
        await storage.delete(row.signature_path)
    except FileNotFoundError:
        # The reference is what the user asked to be rid of; a file that is
        # already gone must not block clearing it.
        pass
    row.signature_path = None
    await db.commit()


async def get_signature_bytes(
    *,
    user_id: uuid.UUID,
    db: AsyncSession,
    storage: StorageProvider,
) -> tuple[bytes, str]:
    """Raw signature bytes + MIME type. Raises ``LookupError`` if none on file."""
    await _get_user(user_id, db)
    row = await _get_settings_row(db)
    path = row.signature_path if row is not None else None
    if not path:
        raise LookupError("No signature on file")
    raw = await storage.read(path)
    content_type = mimetypes.guess_type(path)[0] or "image/png"
    return raw, content_type


# ---------------------------------------------------------------------------
# Render-side resolution — ONE seam per representation, used by every renderer
# ---------------------------------------------------------------------------


async def _signature_path_if_enabled(
    db: AsyncSession, document: DocumentKind, *, override: bool | None = None
) -> str | None:
    """The stored path, or None when the effective toggle is off / nothing is
    on file.

    One function, so neither the kind default nor a document's own override
    can be honoured on the PDF path and ignored on the DOCX path (SF-PDF.6's
    "one resolution seam per document family" control). Never raises: a
    render must not fail because a profile row is missing or a settings row
    has not been created yet.

    ``override`` (F-4b, founder ruling 2026-09-11): the per-document
    ``signature_override`` column a caller reads off its own
    ``GeneratedCV``/``GeneratedCoverLetter`` row. ``None`` (the column's
    default and every pre-F-4b row's only possible value) defers to the
    kind-level toggle below, unchanged; ``True``/``False`` decide the
    question outright, regardless of what the kind default says.
    """
    row = await _get_settings_row(db)
    if row is None:
        # No settings row == no signature has ever been uploaded, so the toggle
        # question does not arise — not even a document override can render an
        # image that was never stored.
        return None

    if override is not None:
        enabled = override
    elif document == "letter":
        # `getattr` with the DEFAULT, not with False: a freshly-created row has
        # not reflected its server_default yet, and the defaults are the
        # convention, not "off". Same shape as review_mode/dismissed_explainers.
        enabled = bool(getattr(row, "signature_in_letter", True))
    else:
        enabled = bool(getattr(row, "signature_in_cv", False))
    if not enabled:
        return None
    return row.signature_path or None


async def resolve_signature_data_uri(
    db: AsyncSession, *, document: DocumentKind, override: bool | None = None
) -> str | None:
    """Inline ``data:`` URI for the HTML/PDF renderers, or None.

    None covers all "do not render" cases identically — override off, kind
    toggle off (when override is None), no signature on file, no profile,
    file deleted after upload — because the template's ``{% if %}`` is the
    only consumer and it cannot act on the difference. A missing FILE is
    silently omitted rather than raising, exactly as the photo is
    (``cv._resolve_photo_data_uri``): a deleted asset must not turn a
    download into a 500.
    """
    from applire.storage import get_storage

    path = await _signature_path_if_enabled(db, document, override=override)
    if not path:
        return None
    try:
        raw = await get_storage().read(path)
    except FileNotFoundError:
        return None
    suffix = Path(path).suffix.lower().lstrip(".")
    mime = _MIME_BY_SUFFIX.get(suffix, "image/png")
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


async def resolve_signature_bytes(
    db: AsyncSession, *, document: DocumentKind, override: bool | None = None
) -> bytes | None:
    """Raw bytes for the DOCX writers (python-docx needs a stream, not a URI)."""
    data_uri = await resolve_signature_data_uri(db, document=document, override=override)
    if data_uri is None:
        return None
    _, _, payload = data_uri.partition(",")
    return base64.b64decode(payload)


async def resolve_signature_available(db: AsyncSession) -> bool:
    """Whether ANY signature image is on file at all, independent of either
    kind toggle or any document's override.

    Not itself part of the ADR-088/F-0 render precedence — this answers a
    different question, the one the frontend's three-state control needs to
    decide whether to render itself at all rather than a disabled hint
    (F-4b: "hidden or disabled ... when no signature is uploaded", brief
    step 3). ``signature_effective`` alone cannot answer it: with no
    per-document override and the kind default off, ``signature_effective``
    reads False whether or not a file was ever uploaded, and the control
    must tell those two cases apart — one has nothing to offer, the other has
    a real choice to make.
    """
    row = await _get_settings_row(db)
    return bool(row and row.signature_path)


async def resolve_signature_effective(
    db: AsyncSession, *, document: DocumentKind, override: bool | None = None
) -> bool:
    """Whether a signature WOULD render for this document right now — the
    ``signature_effective`` field the status/detail responses expose (F-4b)
    so the frontend's three-state control can show the true current state
    without re-fetching or re-downloading.

    Deliberately the same precedence seam as the render path
    (``_signature_path_if_enabled``): a status response that computed this
    differently from what a download actually produces would be its own
    defect. Cheaper than ``resolve_signature_data_uri`` — it never touches
    storage, so a status poll cannot fail or slow down because of a missing
    file; a dangling path still reads as "effective" here exactly as the
    render path's own None-covers-every-case discipline intends (the
    template's ``{% if %}`` is what actually discovers a missing file).
    """
    path = await _signature_path_if_enabled(db, document, override=override)
    return path is not None


def format_place_date(location: str | None, language: str, today: date | None = None) -> str:
    """The CV's closing ``Ort, Datum`` line.

    ``"Berlin, 11. September 2026"`` / ``"Berlin, 11 September 2026"``, falling
    back to the bare date when the profile carries no location — a fabricated
    city on a signed document would be a truthfulness defect, not a cosmetic
    one, so an absent location drops the whole ``Ort,`` part rather than
    guessing or printing a placeholder.

    Deliberately uses the same month spelling as ``utils.letter_date`` so the
    two documents in one application do not date themselves differently.
    """
    from applire.utils.letter_date import format_letter_date

    formatted = format_letter_date(language, today)
    place = (location or "").strip()
    return f"{place}, {formatted}" if place else formatted
