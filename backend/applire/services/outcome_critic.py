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

"""ADR-060 — the outcome critic: ONE engine, TWO mounts (#322, third
amendment 2026-07-31, ADR-066).

**Mounts.** :func:`run_pass_a` — the ASSEMBLED CV, judged alone for
single-document coherence before it is presented (ADR-067 clause 5's
required reader for the CV chain). :func:`run_pass_b` — the assembled CV +
settled letter pair, judged for cross-document coherence. Same engine
(:func:`_run_mount`), same report schema, same contract: advisory-only,
never gates delivery.

**The judgement reads the documents (2026-07-31).** The 2026-07-30 build
sent the model only fact-half nominations; SF-CRITIC.9 observed that
candidate universe missing real findings (achievement figures, scope
qualifiers — shapes no enumeration anticipated). The model now reads the
assembled document(s) verbatim; the presence facts below survive as ANCHORS.
What bounds the widened judgement is CITATION VERIFICATION: every finding
must quote its span(s), verified under normalisation against the named
document before an advisory is built (SF-CRITIC.11) — the model does the
semantic work, code checks the citation.

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
from applire.prompts.outcome_critic import (
    SYSTEM_PROMPT,
    build_pass_a_prompt,
    build_pass_b_prompt,
)
from applire.providers.llm.base import LLMProvider
from applire.schemas.outcome_critic import CriticAdvisory, OutcomeCriticReport
from applire.services.ats_audit import _norm as ats_norm
from applire.services.ats_audit import surface_present
from applire.services.keyword_ledger import _draft_strings, is_positioning_only, split_ledger_for_prompt
from applire.services.letter_figure_guard import _TENURE_RE
from applire.services.oracle.extract import split_sentences

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


def _narrow_to_sentence(unit: str, forms: list[str]) -> str:
    """Narrow a matched document UNIT down to the SENTENCE that carries the
    concept (adversarial pass 2026-07-30, finding 3 / SF-CRITIC.2/.6).

    A CV unit is already one bullet; a letter unit is a whole paragraph
    (``_document_units`` above). Two advisories about two different concepts
    in the SAME paragraph therefore used to quote the ENTIRE paragraph
    twice, byte-identical but for the concept name — the candidate sees the
    same wall of text twice with no way to tell, at a glance, which fact
    each advisory rests on.

    Reuses ``oracle.extract.split_sentences`` (the Oracle's own deterministic
    splitter — no independent copy) rather than a new one. Selecting *which
    sentence contains a given literal string* is a FACT under ADR-062 clause
    1 (settled by string containment alone, no reading for meaning) — this
    function does no summarising or rewriting, and a concept spanning more
    than one sentence still gets exactly the ONE sentence containing its
    surface form, never a paraphrase. Falls back to the whole unit when it
    doesn't split into more than one sentence (already the common case for a
    CV bullet) or when no single sentence contains the match (never expected
    given ``_scan_units`` only calls this on a unit it already matched, but
    fail-open rather than return an empty snippet).
    """
    sentences = split_sentences(unit)
    if len(sentences) <= 1:
        return unit.strip()
    for sentence in sentences:
        sentence_norm = ats_norm(sentence)
        if any(surface_present(f, sentence_norm) for f in forms):
            return sentence.strip()
    return unit.strip()


def _scan_units(units: list[str], forms: list[str]) -> tuple[bool, bool, str | None]:
    """Presence + tenure-qualification of ANY of *forms* across *units*.

    Returns ``(present, qualified, snippet)``. ``snippet`` prefers a
    qualified unit (the one carrying the evidence a judgement would act on)
    over a merely-present one, so the persisted advisory always quotes the
    most informative match — narrowed to the sentence containing the
    concept (:func:`_narrow_to_sentence`), not the whole unit.

    Tenure-qualification itself stays scored at UNIT granularity (does a
    tenure figure sit ANYWHERE in the same paragraph as the concept) —
    narrowing only changes what gets QUOTED, never what gets judged
    ``qualified``, so #322's founding shape (tenure figure and concept in the
    same sentence, in practice) is unaffected either way.
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
            snippet = _narrow_to_sentence(unit, forms)
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


# ── citation verification (SF-CRITIC.11, third amendment 2026-07-31) ───────
# A finding is only surfaced on spans provably in the documents. Verification
# runs under normalisation, NEVER a raw ``in`` check: a model quotes German
# prose with typographic punctuation (U+2019 apostrophes, curly quotes — the
# documented class that defeated an ASCII marker list once already) and may
# reflow whitespace; a naive substring check would silently drop true
# findings, quietly re-narrowing the very control the widened judgement is
# (an invisible recall cliff, not a fail-open bug).

_CITATION_PUNCT_FOLD = str.maketrans(
    {
        "’": "'",  # right single quotation mark (the U+2019 class)
        "‘": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "«": '"',
        "»": '"',
        "–": "-",  # en dash
        "—": "-",  # em dash
        " ": " ",  # no-break space
    }
)


def _normalize_citation(text: str) -> str:
    """Punctuation-fold + the shared ``ats_norm`` fold + whitespace collapse.

    Layered ON TOP of ``ats_norm`` (the module's shared instrument), never
    instead of it — the two folds answer different questions and only their
    composition survives both a typographic quote and a case difference.
    """
    return " ".join(ats_norm(text.translate(_CITATION_PUNCT_FOLD)).split())


def _citation_present(quote: str | None, units: list[str]) -> bool:
    """Is *quote* literally present (under normalisation) in any unit — or in
    the unit-joined text, for a span crossing a sentence boundary within one
    paragraph? Empty/None quotes are NOT present — a finding must cite."""
    if not quote or not quote.strip():
        return False
    q = _normalize_citation(quote)
    if not q:
        return False
    for unit in units:
        if q in _normalize_citation(unit):
            return True
    return False


# ── deterministic, bilingual advisory text (SF-CRITIC.2/.6) ────────────────
# Both languages are ALWAYS built from the same citation-verified quotes —
# DE/EN parity is a construction guarantee, never a per-call accident of
# which language the model happened to answer in. The model contributes the
# judgement (worth_surfacing), the quotes, and a short neutral topic label;
# the narrative the user reads is assembled HERE, from the verified quotes
# only, so the advisory always states the fact it rests on.
_MESSAGES: dict[str, dict[str, str]] = {
    "de": {
        "letter_only": (
            'Ihr Anschreiben nennt "{letter_state}" (zu {concept}); '
            "Ihr Lebenslauf erwähnt dies nicht."
        ),
        "letter_richer": (
            'Ihr Anschreiben nennt zu {concept} "{letter_state}"; Ihr Lebenslauf '
            'nennt {concept} nur ohne diese Angabe ("{cv_state}").'
        ),
        "numeric_inconsistency": (
            'Zu {concept} nennt Ihr Lebenslauf "{cv_state}", Ihr Anschreiben '
            'dagegen "{letter_state}" — die Angaben unterscheiden sich.'
        ),
        "internal_inconsistency": (
            'Ihr Lebenslauf sagt zu {concept} "{cv_state}"; die zugehörige '
            'Detailangabe lautet "{cv_detail}" — die Zusammenfassung geht über '
            "das Detail hinaus."
        ),
        "advice": (
            "Es wurde nichts verändert — Sie entscheiden, ob Sie das Dokument "
            "anpassen oder es so lassen."
        ),
    },
    "en": {
        "letter_only": (
            'Your cover letter states "{letter_state}" (about {concept}); '
            "your CV does not mention it."
        ),
        "letter_richer": (
            'Your cover letter states "{letter_state}" about {concept}; your CV '
            'mentions {concept} without that detail ("{cv_state}").'
        ),
        "numeric_inconsistency": (
            'About {concept}, your CV states "{cv_state}" while your cover '
            'letter states "{letter_state}" — the figures differ.'
        ),
        "internal_inconsistency": (
            'Your CV\'s summary states "{cv_state}" about {concept}; the '
            'underlying detail reads "{cv_detail}" — the summary claims more '
            "than the detail substantiates."
        ),
        "advice": (
            "Nothing has been changed — it is your choice whether to adjust "
            "the document or leave it as is."
        ),
    },
}

_PASS_KINDS: dict[str, frozenset[str]] = {
    "cv": frozenset({"internal_inconsistency"}),
    "letter": frozenset({"letter_only", "letter_richer", "numeric_inconsistency"}),
}


def _build_advisory(
    *,
    kind: str,
    concept: str,
    cv_state: str | None,
    cv_detail: str | None,
    letter_state: str | None,
) -> CriticAdvisory:
    messages: dict[str, str] = {}
    for lang, m in _MESSAGES.items():
        body = m[kind].format(
            concept=concept,
            cv_state=cv_state or "",
            cv_detail=cv_detail or "",
            letter_state=letter_state or "",
        )
        messages[lang] = f"{body} {m['advice']}"
    return CriticAdvisory(
        concept=concept,
        kind=kind,  # type: ignore[arg-type]
        cv_state=cv_state,
        cv_detail=cv_detail,
        letter_state=letter_state,
        changed=False,
        message=messages,
    )


def _advisories_from_judgement(
    result: Any,
    *,
    mount: str,
    cv_units: list[str],
    letter_units: list[str],
) -> tuple[list[CriticAdvisory], int]:
    """Parse the model's findings, verify EVERY quoted span against the
    document it names, and build advisories from the survivors.

    Returns ``(advisories, dropped_citations)``. A malformed envelope raises
    (the caller's retry loop owns that); an individual bad finding never does
    — it is dropped and counted, so one hallucinated quote cannot take down
    the round's real findings (SF-CRITIC.11: drops must be visible, not
    fatal and not silent).
    """
    if not isinstance(result, dict) or not isinstance(result.get("findings"), list):
        raise ValueError(
            "outcome critic: malformed judgement response "
            "(expected {'findings': [...]})"
        )
    advisories: list[CriticAdvisory] = []
    dropped = 0
    for item in result["findings"]:
        if not isinstance(item, dict) or not item.get("worth_surfacing"):
            continue
        kind = item.get("kind")
        if kind not in _PASS_KINDS[mount]:
            dropped += 1
            logger.warning(
                "outcome critic (%s mount): finding kind %r not valid on this "
                "pass — dropped",
                mount, kind,
            )
            continue
        concept = str(item.get("concept") or "").strip()
        cv_quote = item.get("cv_quote")
        cv_detail_quote = item.get("cv_detail_quote")
        letter_quote = item.get("letter_quote")

        # Per-kind citation requirements — every span the advisory will show
        # must verify against the document it is attributed to.
        checks: list[bool] = [bool(concept)]
        if kind == "letter_only":
            checks.append(_citation_present(letter_quote, letter_units))
            cv_quote = None  # by definition absent from the CV
        elif kind == "letter_richer" or kind == "numeric_inconsistency":
            checks.append(_citation_present(letter_quote, letter_units))
            checks.append(_citation_present(cv_quote, cv_units))
        elif kind == "internal_inconsistency":
            checks.append(_citation_present(cv_quote, cv_units))
            checks.append(_citation_present(cv_detail_quote, cv_units))
            letter_quote = None
        if not all(checks):
            dropped += 1
            logger.warning(
                "outcome critic (%s mount): citation verification FAILED for "
                "%r finding %r — dropped, not surfaced (cv_quote=%r, "
                "cv_detail_quote=%r, letter_quote=%r)",
                mount, kind, concept, cv_quote, cv_detail_quote, letter_quote,
            )
            continue
        advisories.append(
            _build_advisory(
                kind=kind,
                concept=concept,
                cv_state=cv_quote,
                cv_detail=cv_detail_quote,
                letter_state=letter_quote,
            )
        )
    return advisories, dropped


def _anchor_dict(fact: ConceptPresenceFact) -> dict[str, str]:
    return {
        "concept": fact.concept,
        "cv_state": fact.cv_snippet if fact.cv_present else "not mentioned in the CV",
        "letter_state": fact.letter_snippet or "",
    }


async def _run_mount(
    *,
    mount: str,
    cv_tailored: dict[str, Any] | None,
    letter_data: dict[str, Any] | None,
    keyword_ledger: list[dict[str, Any]] | None,
    job_role_title: str | None,
    jd_excerpt: str | None,
    provider: LLMProvider,
    enabled: bool | None,
    max_rounds: int | None,
) -> OutcomeCriticReport:
    """ONE critic engine, mounted twice (ADR-066 / ADR-060 third amendment).

    Never raises — every failure mode short-circuits to a distinctly-logged,
    distinctly-reasoned :class:`OutcomeCriticReport` (SF-CRITIC.1/.8) and
    NEVER gates delivery (ADR-060 clause 3 / PO decision 2, unchanged through
    all three amendments).

    ``enabled``/``max_rounds`` default to ``None``, resolved to the CURRENT
    value of the module-level ``CRITIC_ENABLED``/``CRITIC_MAX_ROUNDS`` at
    CALL time (deliberately NOT a bound default — a default value is frozen
    at function-definition time, which would make ``CRITIC_ENABLED`` un-
    patchable by an operator env-var change or a test's ``monkeypatch``
    after import; reading the module global inside the body stays live).

    There is deliberately NO 0-candidate short-circuit any more: the model
    reads the assembled document(s) regardless of what the fact layer found.
    "0 candidates ⇒ no LLM call" WAS SF-CRITIC.9's blindness — the blind
    panel found real asymmetries the candidate universe could not contain.
    """
    if enabled is None:
        enabled = CRITIC_ENABLED
    if max_rounds is None:
        max_rounds = CRITIC_MAX_ROUNDS
    if not enabled:
        logger.info("outcome critic (%s mount): DID NOT RUN (CRITIC_ENABLED=false)", mount)
        return OutcomeCriticReport(ran=False, reason="disabled", mount=mount, advisories=[])
    if not cv_tailored:
        # Both mounts need the assembled CV — Pass A judges it, Pass B
        # cross-checks against it. A precondition failure, never a "found
        # nothing" judgement.
        logger.info(
            "outcome critic (%s mount): DID NOT RUN (no assembled CV)", mount
        )
        return OutcomeCriticReport(ran=False, reason="missing_cv", mount=mount, advisories=[])
    if mount == "letter" and not letter_data:
        logger.info(
            "outcome critic (letter mount): DID NOT RUN (no settled letter draft)"
        )
        return OutcomeCriticReport(
            ran=False, reason="missing_letter", mount=mount, advisories=[]
        )

    cv_units = _document_units(cv_tailored)
    letter_units = _document_units(letter_data) if mount == "letter" else []

    if mount == "letter":
        # The deterministic presence facts ride along as ANCHORS — no longer
        # the input boundary (SF-CRITIC.9). A missing ledger (legacy/pre-E037
        # analysis) empties the anchor list; it no longer blocks the pass,
        # because the judgement's real input is the documents themselves.
        if keyword_ledger:
            facts = compute_presence_facts(cv_tailored, letter_data, keyword_ledger)
            anchors = [_anchor_dict(f) for f in facts if f.flagged]
        else:
            logger.info(
                "outcome critic (letter mount): no Keyword Ledger — judging "
                "with an empty anchor list"
            )
            anchors = []
        prompt = build_pass_b_prompt(
            cv_units, letter_units, anchors, job_role_title, jd_excerpt
        )
    else:
        anchors = []
        prompt = build_pass_a_prompt(cv_units, job_role_title, jd_excerpt)

    attempts = max(1, max_rounds)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = await provider.aparse_json(
                prompt, system=SYSTEM_PROMPT, max_tokens=CRITIC_JUDGEMENT_MAX_TOKENS
            )
            advisories, dropped = _advisories_from_judgement(
                result, mount=mount, cv_units=cv_units, letter_units=letter_units
            )
            logger.info(
                "outcome critic (%s mount): RAN — %d anchor(s), %d advisory(-ies) "
                "surfaced, %d citation-dropped (judgement attempt %d/%d)",
                mount, len(anchors), len(advisories), dropped, attempt, attempts,
            )
            return OutcomeCriticReport(
                ran=True,
                reason=None,
                mount=mount,
                advisories=advisories,
                dropped_citations=dropped,
            )
        except Exception as exc:  # noqa: BLE001 — advisory-only judgement call;
            # a provider/parse error must never fail document generation
            # (never gates delivery). Logged distinctly from the "DID NOT
            # RUN" branches above and from a clean 0-advisory run, so the
            # states are never conflated at the observability layer
            # (SF-CRITIC.1's own lesson).
            last_error = exc
            logger.warning(
                "outcome critic (%s mount): judgement call failed on attempt %d/%d: %s",
                mount, attempt, attempts, exc,
            )

    logger.warning(
        "outcome critic (%s mount): RAN but the judgement call never succeeded "
        "after %d attempt(s) — advisory omitted, delivery unaffected. Last "
        "error: %s",
        mount, attempts, last_error,
    )
    return OutcomeCriticReport(
        ran=True, reason="judgement_error", mount=mount, advisories=[]
    )


async def run_pass_a(
    *,
    cv_tailored: dict[str, Any] | None,
    job_role_title: str | None,
    jd_excerpt: str | None,
    provider: LLMProvider,
    enabled: bool | None = None,
    max_rounds: int | None = None,
) -> OutcomeCriticReport:
    """ADR-060 Pass A (built by the 2026-07-31 amendment): the ASSEMBLED CV,
    judged alone for single-document coherence, before it is presented —
    ADR-067 clause 5's required reader for the CV chain."""
    return await _run_mount(
        mount="cv",
        cv_tailored=cv_tailored,
        letter_data=None,
        keyword_ledger=None,
        job_role_title=job_role_title,
        jd_excerpt=jd_excerpt,
        provider=provider,
        enabled=enabled,
        max_rounds=max_rounds,
    )


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
    """ADR-060 Pass B: the assembled CV + settled letter pair, judged for
    cross-document coherence. Same engine as Pass A (ADR-066)."""
    return await _run_mount(
        mount="letter",
        cv_tailored=cv_tailored,
        letter_data=letter_data,
        keyword_ledger=keyword_ledger,
        job_role_title=job_role_title,
        jd_excerpt=jd_excerpt,
        provider=provider,
        enabled=enabled,
        max_rounds=max_rounds,
    )
