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

"""Deterministic stance guard for the ADR-046 reconciler (#127).

Blind PQ 2026-07-04 showed the reconciler LLM (a) echoing an explicitly DENIED
token into ``add_bullets.technologies`` ("produktionsreife RAG-Erfahrung fehlt
mir aber" → technologies=["…", "RAG"]) and (b) fabricating a skill op from an
answer that never mentioned it (churn answer → upsert_skill Python). The prompt
now carries a stance rule and a ``denials`` envelope; this module is the
deterministic backstop, mirroring ``keyword_ledger._enforce_gap_stance`` (F4):
the model's own denial verdict outranks its ops — never-claim beats claim
(ADR-040) — and interview-turn token claims must be grounded in the turn's
gap+question+answer text.

Matching reuses THE shared presence predicate (``surface_present``, US212) so
the reconciler can never disagree with the ATS/coverage instruments on whether
a token is present.
"""
from __future__ import annotations

import logging
from typing import Any

from applire.services.ats_audit import _norm, surface_present
from applire.services.profile.reconcile.ops import (
    AddBullets,
    ReconcileOp,
    UpsertCertification,
    UpsertLanguage,
    UpsertSkill,
)

logger = logging.getLogger(__name__)


def _is_denied(token: str, denials: list[str]) -> bool:
    """Containment in either direction: 'azure' denies 'Microsoft Azure' and
    'Microsoft Azure' denies 'Azure'."""
    token_norm = _norm(token)
    if not token_norm:
        return False
    return any(
        surface_present(d, token_norm) or surface_present(token, _norm(d))
        for d in denials
    )


def _text_claims_denied(text: str, denials: list[str]) -> bool:
    text_norm = _norm(text)
    return any(surface_present(d, text_norm) for d in denials)


def _grounding_corpus(new_info: Any, source: str) -> str | None:
    """Normalised gap+question+answer text for interview turns; None otherwise.

    Grounding is an interview-turn instrument only (#127 scope decision): a CV
    import reconciles a whole staged extraction, where token presence is
    trivially satisfied and paraphrase is legitimate.
    """
    if source != "interview" or not isinstance(new_info, dict):
        return None
    parts = [str(v) for v in new_info.values() if isinstance(v, str)]
    return _norm(" ".join(parts))


def enforce_stance(
    ops: list[ReconcileOp],
    *,
    denials: list[str],
    new_info: Any,
    source: str,
) -> list[ReconcileOp]:
    """Strip op content that contradicts the model's own denials or, on
    interview turns, claims tokens absent from the turn entirely.

    Scope: token-like claims (skill / technology / language / certification
    names) plus free-text bullets that restate a denied token. Entity upserts
    (work/project/volunteer) stay out of scope — they legitimately echo profile
    knowledge (target merges, alternate titles, rule 7).
    """
    corpus = _grounding_corpus(new_info, source)

    def keep_token(token: str, kind: str) -> bool:
        if denials and _is_denied(token, denials):
            logger.warning(
                "reconcile stance: dropped DENIED %s %r (the model's own denial "
                "verdict outranks its ops, ADR-040/#127)", kind, token,
            )
            return False
        if corpus is not None and not surface_present(token, corpus):
            logger.warning(
                "reconcile stance: dropped ungrounded %s %r — token absent from "
                "the interview turn (#127)", kind, token,
            )
            return False
        return True

    result: list[ReconcileOp] = []
    for op in ops:
        if isinstance(op, UpsertSkill):
            if not keep_token(op.name, "skill"):
                continue
        elif isinstance(op, UpsertLanguage):
            if not keep_token(op.language, "language"):
                continue
        elif isinstance(op, UpsertCertification):
            if not keep_token(op.name, "certification"):
                continue
        elif isinstance(op, AddBullets):
            technologies = [t for t in op.technologies if keep_token(t, "technology")]
            responsibilities = list(op.responsibilities)
            achievements = list(op.achievements)
            if denials:
                dropped = [
                    b
                    for b in responsibilities + achievements
                    if _text_claims_denied(b, denials)
                ]
                for b in dropped:
                    logger.warning(
                        "reconcile stance: dropped bullet restating a denied "
                        "token: %r (#127)", b,
                    )
                responsibilities = [
                    b for b in responsibilities if not _text_claims_denied(b, denials)
                ]
                achievements = [
                    b for b in achievements if not _text_claims_denied(b, denials)
                ]
            if not (technologies or responsibilities or achievements):
                continue
            op = op.model_copy(
                update={
                    "technologies": technologies,
                    "responsibilities": responsibilities,
                    "achievements": achievements,
                }
            )
        result.append(op)
    return result
