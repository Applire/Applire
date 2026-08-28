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

"""The `testimony/1` free-text intake contract (#258, ADR-046/ADR-058/ADR-059).

Testimony is a candidate-authored free-text document ("anything else recruiters
should know") — a whole pasted/uploaded dossier, not an itemized elicited claim.
It runs through the SAME reconcile -> stance -> apply chain as `submit_claims`
and the interview, with a distinct `testimony` provenance marker, so the vault
effect is identical regardless of which door (UI paste box or MCP tool)
submitted it (ADR-058 door-parity invariant). Published as MCP resource
`schema://testimony`.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from applire.schemas.profile import Conflict, FieldChange, PendingConfirmation

TESTIMONY_SCHEMA_VERSION = "testimony/1"

# Generous ceiling for a pasted dossier (several off-CV pages) while still
# bounding a single reconcile call's input size; not a claims-style itemized
# cap (see ClaimItem.statement's 2000 chars — a whole dossier does not fit
# there, which is exactly why this is its own door, not a submit_claims call).
TESTIMONY_MAX_CHARS = 20_000


class TestimonyRequest(BaseModel):
    """One free-text testimony submission, in the candidate's own words."""

    model_config = {"extra": "forbid"}

    text: str = Field(
        min_length=1,
        max_length=TESTIMONY_MAX_CHARS,
        description=(
            "Free-text testimony in the candidate's own words — pasted or "
            "uploaded prose, not itemized claims. This text is the ONLY "
            "grounding corpus for token claims: skills, languages, "
            "certifications and figures not stated here are dropped by the "
            "stance guard, and an explicit denial is recorded, not dropped."
        ),
    )


# ── Result envelope (returned by submit_testimony; not part of the input
#    contract published at schema://testimony) ────────────────────────────────


class NotApplied(BaseModel):
    """One piece of the submitted testimony the committed ops do not carry (#370).

    FACTS only (ADR-062 clause 1) — computed by the deterministic witness
    (`services.profile.reconcile.witness.compute_not_applied`), never a
    judgement about whether the content "really" landed: a span is listed
    because it is not LITERALLY present in any op's payload (or, for
    `kind="op"`, because the model's own raw op failed schema validation at
    parse time), never because a semantic matcher decided it was paraphrased
    away. No item here is proof of loss — corroborating a spelled-out figure,
    or a paraphrase so complete it shares no content word with the ops, both
    read identically to a genuine drop; the caller (or a human) still makes
    that call. See the `witness` module docstring for the exact algorithm.
    """

    model_config = {"extra": "forbid"}

    #: The verbatim testimony text this item is about (a figure's own written
    #: form, or a whole sentence), truncated to 200 chars. For `kind="op"`
    #: there is no testimony excerpt to quote — the loss happened in the
    #: MODEL's raw output, not at a location in the submitted text — so this
    #: carries the rejected op's own declared `"op"` type string instead
    #: (`"<unknown>"` when the raw item carried no string `op` key at all).
    span: str
    kind: Literal["figure", "sentence", "op"]
    #: `figure_not_in_any_op` — a numeric figure in the testimony whose
    #: normalised digit string appears in no op's serialised payload.
    #: `no_op_carried_it` — a testimony sentence sharing no content token (>=5
    #: chars, minus a small stopword set) with any op's serialised payload.
    #: `op_rejected` — a raw op the model emitted failed schema validation at
    #: `engine._parse_ops` and was dropped before ever reaching the applier.
    reason: Literal["figure_not_in_any_op", "no_op_carried_it", "op_rejected"]


class TestimonyResult(BaseModel):
    """The outcome of reconciling one testimony submission into the vault.

    `status` precedence (#370 amendment — `partial` inserted): error >
    needs_confirmation > conflict > partial > applied > denial_recorded >
    no_change. One submission can yield changes AND a confirmation AND a
    conflict AND a denial AND unapplied content all at once; `status` reports
    the single most-actionable outcome while `changes` / `confirmations` /
    `conflicts` / `not_applied` carry the full parallel detail.

    `partial` — at least one change landed (`bool(changes)`) AND
    `not_applied` is non-empty: SOME of the submission is visibly missing.
    `applied` now specifically means "changes landed AND nothing is known to
    be missing" — `applied` no longer means "applied some of it" (#370's ask).

    `no_change` + a POPULATED `not_applied` (#371, folded into #370): the
    reconciler produced no ops at all, but the witness can still point at
    which testimony content shares no token with anything the model emitted —
    distinguishing "genuinely nothing new was said" (`not_applied` empty,
    e.g. "Just saying hello") from "the model was given content and produced
    nothing from it" (`not_applied` populated). `status` itself stays
    `no_change` either way — there is no positive change to elevate it to
    `partial`, which requires `bool(changes)`.

    `not_applied` is computed over the ops the reconcile ENGINE produced
    (post-parse/stance/attribution), not over what the committer/applier
    finally persisted — see `witness.compute_not_applied`'s docstring for the
    exact scope and its known blind spots (e.g. an `add_bullets` op whose
    `target` never resolves at APPLY time is invisible to this witness; that
    is a distinct, not-yet-covered loss mechanism — Part A of #370's trace).
    """

    submission_id: str
    schema_version: str = TESTIMONY_SCHEMA_VERSION
    status: Literal[
        "error",
        "needs_confirmation",
        "conflict",
        "partial",
        "applied",
        "denial_recorded",
        "no_change",
    ]
    changes: list[FieldChange] = Field(default_factory=list)
    confirmations: list[PendingConfirmation] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    #: #370 — which spans of the submission the committed ops do not
    #: literally carry, and why. Populated whenever the witness finds
    #: something, regardless of `status` (including `no_change` — #371).
    #: Empty for a truncated (`status="error"`) submission: nothing was
    #: reconciled at all, so there is no op batch to check content against.
    not_applied: list[NotApplied] = Field(default_factory=list)
    detail: str | None = None
