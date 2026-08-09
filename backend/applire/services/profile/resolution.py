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

"""ADR-063 clause 3 — the conflict/confirmation RESOLUTION intake adapters.

The sibling of `field_edit.py`, and the same shape: raw door payload in, one
typed act out, **pure** — no database, no LLM, no async, no clock. The doors
(the REST `POST /api/profile/conflicts/{id}/resolve` route and the interview's
`_handle_conflict_answer` / `_handle_confirmation_answer` dispatch) each read
the parked item and hand it here; everything about the WRITE then belongs to
`commit_ops` (#480 PR 5, design §4.2 / §4.5).

The adapters take the parked `Conflict` / `PendingConfirmation` **object**, not
loose strings, so the op can never describe a dispute other than the one that
was actually read — the identity the applier re-checks against the profile's
own open-conflict list before it authorises any overwrite.
"""
from __future__ import annotations

from typing import Any

from applire.schemas.profile import Conflict, PendingConfirmation
from applire.services.profile.reconcile.ops import ResolveConfirmation, ResolveField

#: The three ways a dispute can be answered, as the doors have always spelled
#: them. `existing` keeps the stored value, `incoming` accepts the disputed
#: one, `manual` writes the candidate's own text.
VALID_RESOLUTIONS = ("existing", "incoming", "manual")


def build_resolve_field_op(
    conflict: Conflict, *, resolution: str, value: Any = None
) -> ResolveField:
    """Turn an answered dispute into the typed act the committer applies.

    Raises `ValueError` on an unknown `resolution` — the refusal this intake
    has always made, which both doors translate into a 422 / `invalid_input`
    rather than a 500. Shaping errors are caught HERE, before anything reaches
    the write path.

    The op carries the dispute's OWN identity (`section`, `field`,
    `entity_id`), never a caller-supplied location: the applier refuses any op
    whose target does not match the open conflict it names, so an adapter that
    invented a section could not turn one dispute into an overwrite elsewhere.

    `value` is passed through for `manual` only. For `existing`/`incoming` the
    applier reads the winning side off the conflict record itself, so the
    chosen text cannot diverge from the dispute the candidate was shown.
    """
    if resolution not in VALID_RESOLUTIONS:
        raise ValueError(
            f"Invalid resolution '{resolution}'. Must be existing, incoming, or manual."
        )
    return ResolveField(
        conflict_id=conflict.conflict_id,
        target=conflict.entity_id,
        section=conflict.section,
        field=conflict.field,
        value=value if resolution == "manual" else None,
        resolution=resolution,  # type: ignore[arg-type]
    )


def build_resolve_confirmation_op(
    confirmation: PendingConfirmation, chosen_option: str
) -> ResolveConfirmation:
    """Turn an answered N-option confirmation into its typed act.

    No refusals to make: the choice is free text (the interview accepts any
    non-empty answer and maps it deterministically — see
    `session._skill_confirmation_decision`), and an unknown id is the DOOR's
    `LookupError`, raised before this is reached.
    """
    return ResolveConfirmation(
        confirmation_id=confirmation.confirmation_id, chosen_option=chosen_option
    )
