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

"""ADR-060 Pass B — the outcome critic's cross-document coherence pass (#322).

**Scope.** This module builds ONLY Pass B (letter vs. CV cross-document
coherence), per the PO's 2026-07-30 amendment to ADR-060. Pass A (CV
selection) is deliberately NOT built — see the ADR amendment for why (#303's
narrative-presence predicate closes that defect deterministically instead).

**The fact / judgement split (ADR-062 clause 1-2).**

FACT (this module, deterministic, no LLM): for every claimable Keyword-Ledger
concept, whether it is present in the CV's own text, whether it is present in
the letter's own text, and whether either mention co-occurs with a tenure/
depth figure (``letter_figure_guard._TENURE_RE`` — already classified FACT by
that module's own ADR-062 docstring: "does the token immediately after this
number name a unit of years" is settled by the two tokens alone, no reading
for meaning). Presence itself reuses ``ats_audit.surface_present`` — THE
shared instrument ADR-048/US212 requires every consumer to agree on, so this
pass can never disagree with the ATS panel or the coverage guard about
whether a term is "in" a document.

JUDGEMENT (prompts/outcome_critic.py, the model): whether a letter-only or
letter-richer concept is an incoherence worth surfacing to the candidate.
Never computed here.

**Why presence alone is not enough — proof it fires on #322's founding case.**
A naive concept-level presence check does NOT fire on #322: "ISO 9001" is
literally present in both documents, so a bare ``surface_present`` diff
reports nothing. The defect is that only the LETTER's mention carries a depth
qualifier ("zehn Jahre ISO-9001-Audit-Praxis") — the CV's own mention has no
such figure. ``ConceptPresenceFact.letter_richer`` is exactly this shape:
present in both, tenure-qualified in the letter, NOT tenure-qualified in the
CV. See ``tests/unit/test_outcome_critic_facts.py`` for the reproduction and
the passing assertion — this is the one row in the FMEA (SF-CRITIC.9) this
module was built to satisfy, and the fact half provably fires on it.

**Remedy — advisory only, structurally.** :func:`run_pass_b` never receives a
mutable reference to ``GeneratedCV.tailored_data`` or ``GeneratedCoverLetter.
letter_data`` — only plain, already-extracted ``dict`` snapshots — and its
return type, :class:`~applire.schemas.outcome_critic.OutcomeCriticReport`, has
no field a caller could feed back into either document. There is no code path
from this module back into a document; the only possible mistake a caller
could make is not persisting the (read-only) report at all.
"""

import logging
from dataclasses import dataclass
from typing import Any

from applire.constants import (
    CRITIC_ENABLED,
    CRITIC_JUDGEMENT_MAX_TOKENS,
    CRITIC_MAX_ROUNDS,
)
from applire.prompts.outcome_critic import SYSTEM_PROMPT, build_pass_b_prompt
from applire.providers.llm.base import LLMProvider
from applire.schemas.outcome_critic import CriticAdvisory, OutcomeCriticReport
from applire.services.ats_audit import _norm as ats_norm
from applire.services.ats_audit import surface_present
from applire.services.keyword_ledger import _draft_strings, is_positioning_only, split_ledger_for_prompt
from applire.services.letter_figure_guard import _TENURE_RE

logger = logging.getLogger(__name__)

# Not just digits: German tenure prose spells the number out ("zehn Jahre",
# #322's own founding case) — the "any word immediately before the unit"
# shape of ``_TENURE_RE`` already covers that; this module adds nothing new
# to the regex, it only reuses it.


def _document_units(draft: dict[str, Any] | None) -> list[str]:
    """Every non-empty string field of a draft, as separate UNITS (one CV
    bullet, one letter paragraph, one skills entry, ...).

    Reuses ``keyword_ledger._draft_strings`` — the SAME flattener the
    verified-coverage check (US213) and the load-bearing guards already
    scan a draft with, so this fact layer can never disagree with those
    about what text belongs to which document (one shared instrument, per
    the #122 lesson). Unit granularity (not one joined string) is the point:
    the depth-qualifier fact below needs "does a tenure figure sit in the
    SAME unit as the concept", not merely somewhere in the same document.
    """
    if not draft:
        return []
    return [u for u in _draft_strings(draft) if u and u.strip()]


def _concept_forms(entry: dict[str, Any]) -> list[str]:
    forms = list(entry.get("surface_forms") or [])
    if entry.get("concept"):
        forms.append(entry["concept"])
    return [f for f in forms if f]


def _scan_units(units: list[str], forms: list[str]) -> tuple[bool, bool, str | None]:
    """Presence + tenure-qualification of ANY of *forms* across *units*.

    Returns ``(present, qualified, snippet)``. ``snippet`` prefers a
    qualified unit (the one carrying the evidence a judgement would act on)
    over a merely-present one, so the persisted advisory always quotes the
    most informative match.
    """
    present = False
    qualified = False
    snippet: str | None = None
    for unit in units:
        unit_norm = ats_norm(unit)
        if not any(surface_present(f, unit_norm) for f in forms):
            continue
        present = True
        has_tenure = _TENURE_RE.search(unit) is not None
        if snippet is None or (has_tenure and not qualified):
            snippet = unit.strip()
        if has_tenure:
            qualified = True
    return present, qualified, snippet


@dataclass(frozen=True)
class ConceptPresenceFact:
    """One claimable Keyword-Ledger concept's presence shape across both
    documents — the FACT half of Pass B. See module docstring for the split.
    """

    concept: str
    cv_present: bool
    cv_qualified: bool
    cv_snippet: str | None
    letter_present: bool
    letter_qualified: bool
    letter_snippet: str | None

    @property
    def letter_only(self) -> bool:
        """The plain #270-class shape: in the letter, not in the CV at all."""
        return self.letter_present and not self.cv_present

    @property
    def letter_richer(self) -> bool:
        """#322's OWN founding shape: present in BOTH documents, but only the
        letter's mention carries a depth/tenure figure the CV's mention does
        not — the case a bare presence check cannot see."""
        return (
            self.cv_present
            and self.letter_present
            and self.letter_qualified
            and not self.cv_qualified
        )

    @property
    def flagged(self) -> bool:
        return self.letter_only or self.letter_richer


def compute_presence_facts(
    cv_tailored: dict[str, Any] | None,
    letter_data: dict[str, Any] | None,
    keyword_ledger: list[dict[str, Any]] | None,
) -> list[ConceptPresenceFact]:
    """THE fact half of ADR-060 Pass B (ADR-062 clause 2). Deterministic, no LLM.

    Restricted to CLAIMABLE, non-positioning-only ledger entries — the same
    restriction ``verified_missing_claimable`` applies (ADR-048 amended
    2026-07-27): an ``adjacent_evidence`` entry names a capability the
    candidate does NOT literally hold, so it is not a coherence question.
    """
    claimable, _ = split_ledger_for_prompt(keyword_ledger)
    claimable = [e for e in claimable if not is_positioning_only(e)]
    cv_units = _document_units(cv_tailored)
    letter_units = _document_units(letter_data)

    facts: list[ConceptPresenceFact] = []
    for entry in claimable:
        forms = _concept_forms(entry)
        if not forms:
            continue
        cv_present, cv_qualified, cv_snippet = _scan_units(cv_units, forms)
        letter_present, letter_qualified, letter_snippet = _scan_units(letter_units, forms)
        if not letter_present:
            continue  # nothing the letter states — nothing to cross-check
        facts.append(
            ConceptPresenceFact(
                concept=entry.get("concept") or forms[0],
                cv_present=cv_present,
                cv_qualified=cv_qualified,
                cv_snippet=cv_snippet,
                letter_present=letter_present,
                letter_qualified=letter_qualified,
                letter_snippet=letter_snippet,
            )
        )
    return facts


# ── deterministic, bilingual advisory text (SF-CRITIC.2/.6) ────────────────
# Both languages are ALWAYS built from the same facts — DE/EN parity is a
# construction guarantee, never a per-call accident of which language the
# model happened to answer in (the model is never asked to write prose here
# at all; see prompts/outcome_critic.py — it answers one true/false per
# candidate, nothing else).
_MESSAGES: dict[str, dict[str, str]] = {
    "de": {
        "letter_only": (
            'Ihr Anschreiben nennt "{letter_snippet}" (zu {concept}); '
            "Ihr Lebenslauf erwähnt dies nicht."
        ),
        "letter_richer": (
            'Ihr Anschreiben nennt zu {concept} "{letter_snippet}"; Ihr Lebenslauf '
            'nennt {concept} nur ohne diese Angabe ("{cv_snippet}").'
        ),
        "advice": (
            "Es wurde nichts verändert — Sie entscheiden, ob Sie den Lebenslauf "
            "ergänzen oder es so lassen."
        ),
    },
    "en": {
        "letter_only": (
            'Your cover letter states "{letter_snippet}" (about {concept}); '
            "your CV does not mention it."
        ),
        "letter_richer": (
            'Your cover letter states "{letter_snippet}" about {concept}; your CV '
            'mentions {concept} without that detail ("{cv_snippet}").'
        ),
        "advice": (
            "Nothing has been changed — it is your choice whether to add this to "
            "the CV or leave it as is."
        ),
    },
}


def _build_advisory(fact: ConceptPresenceFact) -> CriticAdvisory:
    kind = "letter_only" if fact.letter_only else "letter_richer"
    messages: dict[str, str] = {}
    for lang, m in _MESSAGES.items():
        body = m[kind].format(
            concept=fact.concept,
            letter_snippet=fact.letter_snippet or "",
            cv_snippet=fact.cv_snippet or "",
        )
        messages[lang] = f"{body} {m['advice']}"
    return CriticAdvisory(
        concept=fact.concept,
        cv_state=fact.cv_snippet,
        letter_state=fact.letter_snippet or "",
        changed=False,
        message=messages,
    )


def _advisories_from_judgement(
    result: Any, candidates: list[ConceptPresenceFact]
) -> list[CriticAdvisory]:
    if not isinstance(result, dict) or not isinstance(result.get("findings"), list):
        raise ValueError(
            "outcome critic Pass B: malformed judgement response "
            "(expected {'findings': [...]})"
        )
    by_concept = {c.concept: c for c in candidates}
    advisories: list[CriticAdvisory] = []
    for item in result["findings"]:
        if not isinstance(item, dict):
            continue
        fact = by_concept.get(item.get("concept"))
        if fact is None or not item.get("worth_surfacing"):
            continue
        advisories.append(_build_advisory(fact))
    return advisories


def _candidate_dict(fact: ConceptPresenceFact) -> dict[str, str]:
    return {
        "concept": fact.concept,
        "cv_state": fact.cv_snippet if fact.cv_present else "not mentioned in the CV",
        "letter_state": fact.letter_snippet or "",
    }


async def run_pass_b(
    *,
    cv_tailored: dict[str, Any] | None,
    letter_data: dict[str, Any] | None,
    keyword_ledger: list[dict[str, Any]] | None,
    job_role_title: str | None,
    jd_excerpt: str | None,
    provider: LLMProvider,
    enabled: bool | None = None,
    max_rounds: int | None = None,
) -> OutcomeCriticReport:
    """Run ADR-060 Pass B once for a settled letter draft. Never raises —
    every failure mode short-circuits to a distinctly-logged, distinctly-
    reasoned :class:`OutcomeCriticReport` (SF-CRITIC.1/.8) and NEVER gates
    delivery (SF-CRITIC's "never gates delivery" requirement, ADR-060 clause
    3/PO decision 2, unaffected by the 2026-07-30 amendment).

    ``enabled``/``max_rounds`` default to ``None``, resolved to the CURRENT
    value of the module-level ``CRITIC_ENABLED``/``CRITIC_MAX_ROUNDS`` at
    CALL time (deliberately NOT a bound default — a default value is frozen
    at function-definition time, which would make ``CRITIC_ENABLED`` un-
    patchable by an operator env-var change or a test's ``monkeypatch``
    after import; reading the module global inside the body stays live).
    Callers may still override explicitly, same shape as ``LLM_REVIEW_MAX_
    RETRIES`` threaded through ``review_and_refine``.
    """
    # Every state-defining transition below logs at WARNING, not INFO — see
    # "adversarial pass 2026-07-30, finding 2" (SF-CRITIC.1). The log CALLS
    # were always here and always executed (reproduced against the real
    # generation entrypoint, ``_render_cover_letter_background``, via caplog
    # AND ``--log-cli-level=INFO``, on every branch); the defect was the
    # LEVEL. ``config.py`` documents ``LOG_LEVEL=WARNING`` as a supported
    # value ("applied to all applire.* loggers"), and under that (legitimate)
    # setting an INFO-level "did not run"/"ran, N/M" line is silently
    # dropped while the judgement-failure branch's WARNING survives — so a
    # disabled pass and a working one become indistinguishable (both
    # silent), exactly the shape the real run's eight failed greps show.
    # Promoting to WARNING (not adding a second, redundant log call) puts
    # all three SF-CRITIC.1 states on the same observability tier.
    if enabled is None:
        enabled = CRITIC_ENABLED
    if max_rounds is None:
        max_rounds = CRITIC_MAX_ROUNDS
    if not enabled:
        logger.warning("outcome critic Pass B: DID NOT RUN (CRITIC_ENABLED=false)")
        return OutcomeCriticReport(ran=False, reason="disabled", advisories=[])
    if not letter_data:
        logger.warning("outcome critic Pass B: DID NOT RUN (no settled letter draft)")
        return OutcomeCriticReport(ran=False, reason="missing_letter", advisories=[])
    if not cv_tailored:
        # ADR-060 amended 2026-07-30: Pass B needs BOTH documents. No CV yet
        # (e.g. an agent-authored letter with no linked GeneratedCV) means
        # there is nothing to cross-check against — this is a precondition
        # failure, never a "found nothing" judgement.
        logger.warning(
            "outcome critic Pass B: DID NOT RUN (no generated CV to cross-check "
            "against — cross-document coherence needs both documents)"
        )
        return OutcomeCriticReport(ran=False, reason="missing_cv", advisories=[])
    if not keyword_ledger:
        logger.warning(
            "outcome critic Pass B: DID NOT RUN (no Keyword Ledger — legacy/"
            "pre-E037 job analysis has none)"
        )
        return OutcomeCriticReport(ran=False, reason="missing_ledger", advisories=[])

    facts = compute_presence_facts(cv_tailored, letter_data, keyword_ledger)
    candidates = [f for f in facts if f.flagged]
    if not candidates:
        logger.warning(
            "outcome critic Pass B: RAN — 0 candidate concept(s); no "
            "cross-document incoherence to judge"
        )
        return OutcomeCriticReport(ran=True, reason="no_candidates", advisories=[])

    prompt = build_pass_b_prompt(
        [_candidate_dict(f) for f in candidates], job_role_title, jd_excerpt
    )
    attempts = max(1, max_rounds)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = await provider.aparse_json(
                prompt, system=SYSTEM_PROMPT, max_tokens=CRITIC_JUDGEMENT_MAX_TOKENS
            )
            advisories = _advisories_from_judgement(result, candidates)
            logger.warning(
                "outcome critic Pass B: RAN — %d candidate(s), %d advisory(-ies) "
                "surfaced (judgement attempt %d/%d)",
                len(candidates), len(advisories), attempt, attempts,
            )
            return OutcomeCriticReport(ran=True, reason=None, advisories=advisories)
        except Exception as exc:  # noqa: BLE001 — advisory-only judgement call;
            # a provider/parse error must never fail letter generation
            # (never gates delivery). Logged distinctly from the "DID NOT
            # RUN" branches above and from a clean 0-candidate run, so the
            # three states are never conflated at the observability layer
            # (SF-CRITIC.1's own lesson).
            last_error = exc
            logger.warning(
                "outcome critic Pass B: judgement call failed on attempt %d/%d: %s",
                attempt, attempts, exc,
            )

    logger.warning(
        "outcome critic Pass B: RAN (facts computed for %d candidate(s)) but the "
        "judgement call never succeeded after %d attempt(s) — advisory omitted, "
        "delivery unaffected. Last error: %s",
        len(candidates), attempts, last_error,
    )
    return OutcomeCriticReport(ran=True, reason="judgement_error", advisories=[])
