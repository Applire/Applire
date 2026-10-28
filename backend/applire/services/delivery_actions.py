# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
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
"""ADR-099 cl. 6/9 — the corrector actions the review surface offers on ONE
finding of a delivered document (WP-B, for WP-C's resolvers).

Each action is a model edit of ONE sentence (never a code rewrite of prose,
ADR-082/094 cl. 1), saved through the section-editor write, followed by the
full document re-audit (the same function the surface reads, ADR-099 cl. 4(b)),
and returns the new report. User-triggered, shown, re-audited and reversible
(the ADR-090 cl. 3 carve-out ADR-099 cl. 9 extends to translation).

Contract for the caller (WP-C, ``services/review_actions.py``):

* the caller holds ``review_state.document_lock(kind, doc_id)`` and records the
  review decision (``with_decision``) with ``result.changes`` as the undo
  sections — these functions never touch ``review_state``;
* ``finding_key`` is the ``review_state.finding_key`` of a group-1 row
  (``oracle:<norm>`` / ``ats:<norm>``) as the live report lists it;
* every function raises :class:`ActionNotApplicable` (→ 409) when the finding
  is no longer listed, the station is not one of :func:`owning_stations`, or
  there is nothing to change; ``RewriteFailed`` (→ 502) when the model's edit
  did not pass its fact check (nothing is written then).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

Kind = Literal["cv", "cover_letter"]
NarrowMode = Literal["remove", "narrow", "as_goal"]


class ActionNotApplicable(LookupError):
    """The finding / station / span the action names is not (or no longer) there."""


class RewriteFailed(RuntimeError):
    """The model edit returned nothing usable; nothing was written."""


@dataclass(frozen=True)
class StationOption:
    """One answer of "Wohin gehört das?" — a station that OWNS the evidence the
    Oracle found for a misattributed / borrowed claim (from the verdict's
    evidence refs, never from text)."""

    #: the section id the sentence moves into (CV: ``position::<tailored
    #: position id>`` as ``cv_section_editor`` names it; letter: the work
    #: entry id — the letter has one ``body`` section, the move rewrites the
    #: sentence to name that station).
    station_id: str
    #: profile entry id (``WorkEntry.id`` / project id) — stable across versions.
    experience_id: str | None
    #: structured parts for the UI to compose, never a server sentence.
    title: str
    organization: str


@dataclass
class DeliveryActionResult:
    record: Any                      # refreshed GeneratedCV / GeneratedCoverLetter
    report: dict | None              # record.ats_report after the re-audit
    truthfulness: dict | None        # record.truthfulness_report after the re-audit
    changes: list[dict] = field(default_factory=list)   # [{"section_id","before","after"}] — undo
    still_listed: bool = False       # finding_key still listed in the live report
    llm_calls: int = 0


async def owning_stations(
    kind: Kind, doc_id: uuid.UUID, finding_key: str, db, *, user_id: uuid.UUID | None = None
) -> list[StationOption]:
    """The stations that own the evidence of a ``misattributed`` /
    ``restatementBorrowed`` finding — the answers of the "Wohin gehört das?"
    card (plus the surface's own "Entfernen"). ``[]`` when the verdict carries no
    owned evidence (then the only end state is removal)."""
    raise NotImplementedError


async def move_sentence(
    kind: Kind,
    doc_id: uuid.UUID,
    finding_key: str,
    station_id: str,
    db,
    provider,
    *,
    user_id: uuid.UUID | None = None,
) -> DeliveryActionResult:
    """Move the finding's sentence to ``station_id`` (one of
    :func:`owning_stations`): the corrector takes it out of its current section
    and writes it under the owning station (CV), or rewrites the one letter
    sentence to name that station; then re-audits."""
    raise NotImplementedError


async def narrow_or_remove(
    kind: Kind,
    doc_id: uuid.UUID,
    finding_key: str,
    db,
    provider,
    *,
    mode: NarrowMode = "remove",
    user_id: uuid.UUID | None = None,
) -> DeliveryActionResult:
    """``remove`` — take the claim out (whole sentence, or only the named figures
    for a ``figuresNoEvidence`` verdict); ``narrow`` — rewrite it to what the
    profile backs; ``as_goal`` — rewrite an ``inflatedAspirational`` claim as the
    goal the profile states ("Ist das erreicht?" → Nein). Re-audits."""
    raise NotImplementedError


async def translate_spans(
    kind: Kind,
    doc_id: uuid.UUID,
    db,
    provider,
    *,
    spans: list[str] | None = None,
    user_id: uuid.UUID | None = None,
) -> DeliveryActionResult:
    """"Diese Stellen übersetzen": rewrite ONLY the sentences the failed
    ``document-language`` check names (``spans`` = a subset of them; None = all)
    into the document language, then re-audit."""
    raise NotImplementedError
