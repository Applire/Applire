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

"""Fact pins (E056 / ADR-077) — the user's seat at the budget table.

A fact pin is a verbatim vault quote plus the entry's persisted id, stored on
``applications.pinned_facts``. Hierarchy: truth > pin > budget — a pin is a
*rendering priority*, never evidence: it has no ledger/status effect and no
Oracle exemption.

This module owns the pin store: fail-closed quote verification at write time
(the shared ``_norm_quote`` fold, ADR-070 clause 1), the ``MAX_FACT_PINS``
cap, idempotent removal, and staleness as a recomputed measurement
(clause 7 — a no-longer-resolving pin is excluded and surfaced, never
auto-deleted). Reach into generation (clauses 3–5) lives with the writers,
``bullet_cuts`` and ``ats_audit``, not here.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.constants import MAX_FACT_PINS
from applire.schemas.application import AddFactPinRequest, FactPin
from applire.schemas.profile import MasterProfileData
from applire.services.scope_requirements import _norm_quote

# Vault address book: entry_type -> (MasterProfileData list attr, the entry's
# own content fields a quote may resolve against — ADR-077 clause 1: prose
# bullets for engagements, name/title/statement fields for the scalar types).
_SECTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "work": ("work_experience", ("responsibilities", "achievements")),
    "project": ("projects", ("responsibilities", "achievements")),
    "volunteer": ("volunteer_activities", ("responsibilities", "achievements")),
    "signature_story": (
        "signature_stories",
        ("challenge", "mechanism", "outcome", "benchmark"),
    ),
    "skill": ("skills", ("name",)),
    "certification": ("certifications", ("name",)),
    "education": ("education", ("institution", "degree", "field", "thesis_title")),
    "language": ("languages", ("language", "level")),
    "publication": ("publications", ("title",)),
}



#: ADR-077 amended 2026-08-26 (#580, clause 1 correction): entry types the CV never
#: renders — `TailoredCVData` has no volunteering or publication section. A `cv`
#: target on them is a pin that cannot fire (SF-PIN.9); refused at pin time on
#: both doors (one service gate). The `letter` target stays available.
CV_UNRENDERABLE_PIN_TYPES: frozenset[str] = frozenset({"volunteer", "publication"})


def check_target_renderable(request) -> None:
    """Refuse a target the document cannot render (ValueError → 422 on both doors)."""
    if "cv" in request.targets and request.entry_type in CV_UNRENDERABLE_PIN_TYPES:
        raise ValueError(
            f"The CV has no section for {request.entry_type} entries, so a 'cv' "
            "target could never be met — pin it for the letter instead."
        )

def _find_entry(profile: MasterProfileData, entry_type: str, entry_id: str):
    section, _ = _SECTIONS[entry_type]
    for entry in getattr(profile, section, []) or []:
        if getattr(entry, "id", None) == entry_id:
            return entry
    return None


def entry_is_claimable(entry) -> bool:
    """ADR-077 clause 2 — the claim gate runs ABOVE pins.

    An `unconfirmed` or `denied` entry cannot back a CV line or a letter
    sentence (ADR-061 clause 3 / amendment #485); a pin on it would launder
    the entry past that gate through the PINNED FACTS block. Types without a
    status field are always claimable.
    """
    return getattr(entry, "status", None) not in ("unconfirmed", "denied")


def quote_resolves_in_entry(quote: str, entry, entry_type: str) -> bool:
    """The fact of normalized-quote containment (ADR-062 clause 1 discipline)."""
    quote_norm = _norm_quote(quote)
    if not quote_norm:
        return False
    _, fields = _SECTIONS[entry_type]
    for field in fields:
        value = getattr(entry, field, None)
        nodes = value if isinstance(value, list) else [value]
        for node in nodes:
            if isinstance(node, str) and quote_norm in _norm_quote(node):
                return True
    return False


def pin_resolves(pin: FactPin, profile: MasterProfileData) -> bool:
    entry = _find_entry(profile, pin.entry_type, pin.entry_id)
    return (
        entry is not None
        and entry_is_claimable(entry)
        and quote_resolves_in_entry(pin.quote, entry, pin.entry_type)
    )


def refresh_pin_staleness(
    pins: list[FactPin], profile: MasterProfileData
) -> tuple[list[FactPin], bool]:
    """Recompute ``stale`` on every pin against the current vault.

    Both directions: a pin whose quote stopped resolving becomes stale; a
    stale pin whose quote resolves again heals. Staleness is a measurement of
    the vault, not a stored verdict — nothing is deleted either way.
    """
    changed = False
    refreshed: list[FactPin] = []
    for pin in pins:
        stale = not pin_resolves(pin, profile)
        if stale != pin.stale:
            pin = pin.model_copy(update={"stale": stale})
            changed = True
        refreshed.append(pin)
    return refreshed, changed


def load_pins(application) -> list[FactPin]:
    """Parse the JSONB list (NULL = pre-migration row = no pins)."""
    return [FactPin.model_validate(p) for p in (application.pinned_facts or [])]


async def _load_profile(db: AsyncSession) -> MasterProfileData:
    from applire.models.profile import MasterProfile

    result = await db.execute(
        select(MasterProfile)
        .where(MasterProfile.deleted_at.is_(None))
        .order_by(MasterProfile.created_at.desc())
        .limit(1)
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise LookupError("No profile found — import a CV first")
    return MasterProfileData.model_validate(record.profile_json)


async def add_fact_pin(
    application_id: uuid.UUID,
    user_id: uuid.UUID,
    request: AddFactPinRequest,
    db: AsyncSession,
) -> FactPin:
    """Additive pin write, fail-closed (ADR-077 clauses 1 + 6).

    Raises LookupError (404) for a missing application/profile and ValueError
    (422) for everything the contract forbids: unknown entry, non-resolving
    quote, duplicate, cap.
    """
    from applire.services.application import _get_or_404, _touch

    # Pure shape refusal first (the `build_replace_section_op` precedent): a
    # target the document cannot render is refused before anything is read.
    check_target_renderable(request)

    app = await _get_or_404(application_id, user_id, db)
    profile = await _load_profile(db)

    entry = _find_entry(profile, request.entry_type, request.entry_id)
    if entry is None:
        raise ValueError(
            f"No {request.entry_type} entry with id {request.entry_id} in the vault."
        )
    if not entry_is_claimable(entry):
        raise ValueError(
            "This entry is not claimable (unconfirmed or retracted) — a pin "
            "cannot carry it past the claim gate (truth > pin)."
        )
    if not quote_resolves_in_entry(request.quote, entry, request.entry_type):
        raise ValueError(
            "The quote does not resolve inside the referenced entry's own "
            "content — only vault-backed facts are pinnable."
        )

    pins = load_pins(app)
    if len(pins) >= MAX_FACT_PINS:
        raise ValueError(
            f"At most {MAX_FACT_PINS} fact pins per application (MAX_FACT_PINS)."
        )
    quote_norm = _norm_quote(request.quote)
    if any(
        p.entry_id == request.entry_id and _norm_quote(p.quote) == quote_norm
        for p in pins
    ):
        raise ValueError("This fact is already pinned on this application.")

    pin = FactPin(
        entry_type=request.entry_type,
        entry_id=request.entry_id,
        quote=request.quote,
        targets=list(request.targets),
    )
    app.pinned_facts = [p.model_dump(mode="json") for p in [*pins, pin]]
    _touch(app)
    await db.commit()
    return pin


async def remove_fact_pin(
    application_id: uuid.UUID,
    user_id: uuid.UUID,
    pin_id: str,
    db: AsyncSession,
) -> None:
    """Idempotent removal — deleting an absent pin_id is not an error."""
    from applire.services.application import _get_or_404, _touch

    app = await _get_or_404(application_id, user_id, db)
    pins = load_pins(app)
    remaining = [p for p in pins if p.pin_id != pin_id]
    if len(remaining) == len(pins):
        return
    app.pinned_facts = [p.model_dump(mode="json") for p in remaining]
    _touch(app)
    await db.commit()
