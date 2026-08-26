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

"""The vault as a MODEL sees it — content, not bookkeeping (ADR-078, #593).

``MasterProfile.profile_json`` carries two different kinds of thing under one
key. Most of it is the candidate's **content**: work history, education,
skills, publications, signature stories — the material a document is written
from. A smaller part is the vault's **bookkeeping**:
``metadata.enrichment_history`` (the durable audit trail of every write),
``metadata.pending_conflicts`` / ``pending_confirmations`` (reconciler parks
awaiting a user choice), the completeness score, the creation stamps, and the
``_meta`` sidecar of candidate-marked N/A suppressions (#505). The bookkeeping
exists so the *system* can explain itself to the candidate. Nothing ever
decided that a model should see it — it rode along because the whole JSON was
serialised at the seam.

Measured on the dev profile 2026-08-26, after E055 gave every profile section a
structured editor writing a full old/new receipt per save: 138,946 of 144,624
chars were ``metadata.enrichment_history`` (91 records) against 5,391 chars of
actual content, and one CV generation logged nine calls at the LLM debug log's
200,000-char field cap. See ADR-078 for the full measurement table.

**This filter is not the sibling one.** ``reconcile.stance.exclude_unconfirmed``
(ADR-061 clause 3) removes unconfirmed *content* and is applied to the LLM
**and** to every deterministic pass, because an unconfirmed entry may back
neither. This one removes *bookkeeping* and is applied to **prompts only** —
the deterministic passes legitimately read ``metadata`` (the Keyword Ledger
builds its affirmation corpus from the enrichment trail,
``keyword_ledger._strip_denial_text``; the STATED LIMITS block is rendered from
``denied_concepts``). Applying either at the other's seam is a defect; both are
named at their call sites for that reason.

ADR-062 clause 1 classification: this computes a **fact** — which keys are
bookkeeping is settled by the data structure alone, with no prose read.
"""

from __future__ import annotations

from typing import Any

#: Keys under ``metadata`` a prompt MAY carry (ADR-078 clause 2 — an allowlist,
#: because the failure this exists to prevent is a *new* key silently riding in,
#: and only an allowlist fails in the safe direction).
#:
#: ADR-078 clause 3: a key may be added here ONLY with the prompt that reads it
#: named below. The classification is invisible in a diff and obvious here.
#:
#: * ``denied_concepts`` — the candidate's persisted denials (ADR-059). Read by
#:   ``prompts/review_cv_tailoring`` (check 6's grounding boundary: "text inside
#:   STATED LIMITS or any denial record NEVER grounds anything") and carried as
#:   the profile-side counterpart of the STATED LIMITS block both writers get.
PROMPT_FACING_METADATA_KEYS: frozenset[str] = frozenset({"denied_concepts"})

#: Keys under ``metadata`` that are bookkeeping and never reach a model.
#: Kept explicit rather than derived so that clause 6's totality test can prove
#: EVERY field of :class:`applire.schemas.profile.ProfileMetadata` has been
#: classified — a field added later belongs in one set or the other, and the
#: suite fails until somebody decides which.
PROMPT_EXCLUDED_METADATA_KEYS: frozenset[str] = frozenset(
    {
        "enrichment_history",
        "pending_conflicts",
        "pending_confirmations",
        "completeness_score",
        "created_at",
        "created_via",
        "last_updated",
        "application_count",
    }
)

#: Top-level keys dropped wholesale. ``_meta`` (#505) is the candidate's own
#: completeness-gap suppressions (``na_fields``) — bookkeeping by its own
#: docstring, never content, and ``extra="allow"`` means it can grow keys we
#: have never seen. Its readers (``services/profile/completeness``,
#: ``services/profile/health``) take it off the RAW JSON and are unaffected.
PROMPT_EXCLUDED_TOP_LEVEL_KEYS: frozenset[str] = frozenset({"_meta"})


def prompt_profile_view(
    profile_json: Any,
    *,
    keep: frozenset[str] = PROMPT_FACING_METADATA_KEYS,
) -> Any:
    """Return a filtered COPY of ``profile_json`` for model consumption.

    ADR-078 clause 1: the single place that decides what bookkeeping a prompt
    may carry (ADR-066 — one logical operation, one implementation). The
    candidate's persisted profile is never touched.

    ``keep`` is the narrowing hook of clause 4: a chain that needs a STRICTER
    view declares it as an argument at its call site rather than growing a
    second implementation. ``prompts/gap_analysis`` passes ``frozenset()`` —
    it excludes ``metadata`` entirely, denials included, because a denial's own
    text token-matches *for* the skill it denies and would read as evidence of
    it (the F4 fix).

    Tolerant of ``None`` and malformed shapes at every level: a prompt-input
    filter must never become a new way for generation to fail, so anything it
    cannot recognise as a profile is returned unchanged.
    """
    if not isinstance(profile_json, dict):
        return profile_json

    view = {
        k: v
        for k, v in profile_json.items()
        if k not in PROMPT_EXCLUDED_TOP_LEVEL_KEYS
    }

    metadata = view.get("metadata")
    if "metadata" in view:
        if isinstance(metadata, dict):
            kept = {k: v for k, v in metadata.items() if k in keep}
            if kept:
                view["metadata"] = kept
            else:
                # Clause 2: remove the key rather than leave an empty object —
                # an empty `metadata: {}` is noise in the prompt and reads as
                # "the candidate has no history", which is a different claim.
                del view["metadata"]
        else:
            # Not a dict → nothing allowlistable can be extracted, and the
            # value is bookkeeping-shaped by position. Drop it.
            del view["metadata"]

    return view
