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

"""ADR-063 clause 3 — the `FieldEdit` intake adapter.

A manual section edit arrives as raw door payload (a REST PATCH body, an MCP
`update_profile` argument, the CV section editor's `(section, value)` pair) and
has to become the one typed act the committer understands: a `ReplaceSection`
op. That translation is what this module is, and it is deliberately **pure** —
no database, no LLM, no async, no clock. It can therefore be unit-tested
exhaustively, which is the point ADR-063 makes about adapters generally: the
part that decides *what the user meant* should never be entangled with the part
that decides *what the vault guarantees*.

`cv_section_editor.build_section_field_edit` is the sibling adapter one layer
up: it turns an edited CV section into the same `(section, value)` pair this
one turns into an op. The chain is
document edit → `build_section_field_edit` → `patch_profile_section` →
`build_replace_section_op` → `commit_ops`, which is how the section editor
inherits the committer's invariant set without knowing it exists.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from applire.schemas.profile import OBJECT_SECTIONS, VAULT_SECTIONS
from applire.services.profile.reconcile.ops import ReplaceSection


def decode_section_payload(value: Any) -> Any:
    """Decode a JSON string payload; anything else passes through untouched.

    Guards against a door sending double-encoded data (the frontend has done
    this). Kept verbatim from the PATCH intake — a payload that is genuinely a
    string and not JSON stays a string, and the schema round-trip rejects it if
    the section cannot hold one.
    """
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return value
    return value


def build_replace_section_op(
    section: str, value: Any, *, basis_updated_at: "datetime | None" = None
) -> ReplaceSection:
    """Turn a manual section edit into the typed act the committer applies.

    Raises `ValueError` on an unknown section or on an object-section payload
    that is not an object — the two refusals the PATCH intake has always made,
    and both doors already translate a `ValueError` into a 422 / `invalid_input`
    rather than a 500. Shaping errors are caught HERE, before anything reaches
    the write path: the committer's job is to apply an act, not to interrogate
    a payload.

    The section vocabulary is `VAULT_SECTIONS`, which does not contain
    `metadata` — so no manual edit can address `denied_concepts`,
    `enrichment_history` or the parked lists (ADR-063 amended 2026-08-09
    clause 1). The op re-validates the same set; this check exists so the
    message the user sees names the sections they may actually edit.
    """
    if section not in VAULT_SECTIONS:
        raise ValueError(
            f"Invalid section '{section}'. Valid: {sorted(VAULT_SECTIONS)}"
        )

    decoded = decode_section_payload(value)

    if section in OBJECT_SECTIONS and not isinstance(decoded, dict):
        raise ValueError(
            f"Section '{section}' expects an object; supplied keys are merged "
            f"(an explicit null clears a field)."
        )

    # `photo_url` is owned by the photo endpoints (services/photo.py: upload
    # records GDPR consent, delete removes the stored file). Through the
    # section door a null would orphan the file and a foreign URL would end
    # up in the CV's <img src> (rendered by headless Chromium) — refused on
    # both doors (adversarial finding 2026-08-26; ADR-063 cl. 8(e) family:
    # a field another writer owns is not reachable by a section replace).
    if section == "personal_info" and isinstance(decoded, dict) and "photo_url" in decoded:
        raise ValueError(
            "personal_info.photo_url is managed by the photo endpoints "
            "(POST/DELETE /api/profile/photo); omit it from a section edit."
        )

    return ReplaceSection(section=section, value=decoded, basis_updated_at=basis_updated_at)
