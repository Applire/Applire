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

"""Stance guard for the ADR-046 reconciler (#127), extended by ADR-061.

Blind PQ 2026-07-04 showed the reconciler LLM (a) echoing an explicitly DENIED
token into ``add_bullets.technologies`` ("produktionsreife RAG-Erfahrung fehlt
mir aber" → technologies=["…", "RAG"]) and (b) fabricating a skill op from an
answer that never mentioned it (churn answer → upsert_skill Python). The prompt
now carries a stance rule and a ``denials`` envelope; this module is the
deterministic backstop, mirroring ``keyword_ledger._enforce_gap_stance`` (F4):
the model's own denial verdict outranks its ops — never-claim beats claim
(ADR-040) — and interview-turn token claims must be grounded in the turn's
gap+question+answer text.

Coverage matching reuses THE shared presence predicate (``surface_present``,
US212) so the reconciler can never disagree with the ATS/coverage instruments
on whether a token is present — and ``surface_present`` stays untouched by
ADR-061 for exactly this reason (clause 1).

**ADR-061 (2026-07-27, closes #305/#304).** Charter run #7 case 2 showed
``surface_present``'s literal substring match silently dropping FIVE
morphologically-distant-but-true German claims in one run ("PP" scoped by an
earlier "SAP-Rollout" clause vs. op "SAP PP"; bare "OEE" vs. op "OEE (Overall
Equipment Effectiveness)"; "Sauberraumbereich" vs. op "Sauberraum-Management").
Coverage ("is this token present?") and testimony ("did the candidate say
this?") are different questions — one predicate answering both was the root
error. This module now answers them separately:

* ``_grounded`` / ``surface_present`` — coverage, literal, unchanged, still the
  ATS/ledger/ADR-051 §3 instrument.
* ``_testimony_status`` — the NEW, explicitly-named testimony predicate: a
  deterministic accept path (falls through to ``_grounded``, so every
  literal/aliased match costs nothing extra), then LLM adjudication of the
  uncertain band ONLY, with the citation verified deterministically
  (``_citation_verified``) before an adjudication is ever trusted. Provider
  outage, timeout, malformed JSON, or a failed citation check all fall back to
  ``unconfirmed`` — never ``confirmed`` (asymmetric by design).

``enforce_stance`` is now ``async`` to carry that adjudication call. Its verb
is no longer only ``drop``: a same-turn ``UpsertSkill`` / ``UpsertLanguage`` /
``UpsertCertification`` op is ALWAYS kept once past the denial check, carrying
``status="confirmed"`` or ``status="unconfirmed"`` (clause 3) — the guard
stops deleting testimony it cannot verify and instead writes it to the vault
as a third, non-claimable, candidate-confirmable state. Free-text token lists
(``AddBullets.technologies``) have no per-item status slot to carry that on,
so they keep the old binary keep/drop (an unconfirmed technology tag is
dropped, exactly as before ADR-061) — the fix there is that morphological
misses now reach ``_testimony_status``'s LLM adjudication instead of dying on
the deterministic check alone. Denials are unaffected by any of this: a
denied token is still stripped outright everywhere (ADR-040, never-claim
outranks claim).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from applire.constants import STANCE_ADJUDICATION_MAX_TOKENS
from applire.prompts.stance_adjudication import (
    STANCE_ADJUDICATION_SYSTEM_PROMPT,
    build_stance_adjudication_prompt,
)
from applire.schemas.profile import DeniedConcept, FieldChange, ProfileMetadata
from applire.services.ats_audit import _fold_variants, _norm, surface_present
from applire.services.profile.reconcile.ops import (
    AddBullets,
    ReconcileOp,
    UpsertCertification,
    UpsertLanguage,
    UpsertSkill,
    UpsertStory,
)

if TYPE_CHECKING:
    from applire.providers.llm.base import LLMProvider

logger = logging.getLogger(__name__)

# ADR-061 clause 8 — every drop/downgrade logs the turn text, bounded so one
# oversized testimony (a pasted dossier) can't blow out a log line.
_LOG_TURN_MAX = 500


def _log_turn(raw_turn: str | None) -> str:
    if not raw_turn:
        return ""
    return raw_turn if len(raw_turn) <= _LOG_TURN_MAX else raw_turn[:_LOG_TURN_MAX] + "…"


# ── #207: same-skill-by-another-name aliases ─────────────────────────────────
# The reconciler LLM canonicalizes surface forms on its own ("Postgres" →
# "PostgreSQL"), which defeats a literal membership check against the
# statement. Curated, deterministic alias groups (normalized forms) bridge the
# gap in BOTH directions: an alias in the statement grounds the canonical
# claim, and a denial reaches every alias of the denied concept. Conservative
# and additive by design — only pairs that are unambiguously the same skill.
_ALIAS_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"postgresql", "postgres"}),
    frozenset({"kubernetes", "k8s"}),
    frozenset({"javascript", "js"}),
    frozenset({"typescript", "ts"}),
    frozenset({"mongodb", "mongo"}),
    frozenset({"node.js", "nodejs", "node"}),
    frozenset({"amazon web services", "aws"}),
    frozenset({"google cloud platform", "gcp"}),
    frozenset({"natural language processing", "nlp"}),
    frozenset({"scikit learn", "sklearn"}),
)
# Deliberately ABSENT: ml/"machine learning" and ai/"artificial intelligence" —
# "ml" is also milliliters ("500 ml per cycle") and "ai" appears in domains
# (.ai); either would let real testimony ground a fabricated skill
# (adversarial finding N1, 2026-07-19).
_ALIASES: dict[str, frozenset[str]] = {
    form: group for group in _ALIAS_GROUPS for form in group
}


def _alias_forms(norm: str) -> frozenset[str]:
    """The token's alias group (itself included)."""
    return _ALIASES.get(norm, frozenset({norm}))


def _word_present(form_norm: str, text_norm: str) -> bool:
    """Word-boundary presence — aliases are often short ("js", "ai"), so the
    substring generosity of ``surface_present`` would false-match inside other
    words ("JSON", "maintain")."""
    return (
        re.search(
            rf"(?<![a-z0-9]){re.escape(form_norm)}(?![a-z0-9])", text_norm
        )
        is not None
    )


def _grounded(token: str, corpus: str) -> bool:
    """Token presence via THE shared predicate, widened by the alias groups
    (word-boundary matched) so a canonicalized op survives when the statement
    used a known alias (#207)."""
    if surface_present(token, corpus):
        return True
    token_norm = _norm(token)
    return any(
        _word_present(a, corpus) for a in _alias_forms(token_norm) if a != token_norm
    )


def _independently_affirmed(token: str, denials: list[str], corpus: str) -> bool:
    """Does the corpus affirm ``token`` OUTSIDE every denied compound?

    Every denial occurrence is blanked from the corpus first, so a token that
    only ever appears as part of a denied compound ("…never used Tailwind
    CSS…") is NOT affirmed, while an independent mention ("…in plain CSS…")
    is. Blanking with spaces can only remove matches, never create them, so
    the check stays fail-closed. Adversarial hardening (2026-07-19):

    * Longest denial first — blanking "tailwind" before "tailwind css" left
      an orphaned " css" that read as an affirmation (finding B1).
    * Run-together spellings of a compound denial ("tailwindcss") are blanked
      too, and the affirmation itself is word-boundary matched so a residual
      substring ("…tailwindcss…", "SCSS") never counts (finding B2).
    """
    stripped = corpus
    for d_norm in sorted((_norm(d) for d in denials), key=len, reverse=True):
        if not d_norm:
            continue
        for v in _fold_variants(d_norm):
            stripped = stripped.replace(v, " ")
            if " " in v:
                stripped = stripped.replace(v.replace(" ", ""), " ")
    return any(_word_present(v, stripped) for v in _fold_variants(_norm(token)))


def _bounded_present(form: str, text_norm: str) -> bool:
    """Word-boundary variant of ``surface_present`` for the denial-matching
    predicate (adversarial pass, 2026-07-23): a candidate denied "machine
    learning model training" while explicitly REAFFIRMING AI/ML integration
    work in the same statement, and the ledger floor force-killed the
    unrelated, JD-required concept "AI/ML" because ``surface_present``'s bare
    substring search let "ai" collide inside "tr-ai-ning" — the exact
    collision class #207 deliberately excludes ml/ai from ``_ALIAS_GROUPS``
    for. The grounding side (``_grounded``/``_word_present``) was already
    boundary-guarded; the denial-side containment check was not.

    Mirrors ``surface_present``'s fold-variants (plural morphology) but
    requires every variant to hit at a word boundary (reuses ``_word_present``
    — one instrument, not a second matcher). A denial that names a token as a
    genuine whole word ("I have no AI experience") still matches; only the
    ambiguous embedded-substring collision is excluded.
    """
    n = _norm(form)
    if not n:
        return False
    return any(_word_present(v, text_norm) for v in _fold_variants(n))


def _is_denied(token: str, denials: list[str], corpus: str | None) -> bool:
    """Is the token covered by the model's own denial verdict?

    * Denial contained in the token or an alias of the token → denied:
      'azure' denies 'Microsoft Azure', 'Kubernetes' denies 'K8s' (via the
      token's alias form). The DENIAL itself is never alias-expanded — the
      groups are symmetric, so token-side expansion already covers 'K8s'
      denying 'Kubernetes', and expanding the denial re-introduced substring
      sibling kills ('JavaScript' → alias 'js' ⊂ 'json'; finding O3).
    * Token strictly inside the denied compound ('CSS' ⊂ 'Tailwind CSS') →
      denied ONLY if the corpus never affirms the token outside the compound
      (#207: a denial of the compound is not a denial of the part). Without a
      grounding corpus (cv_upload/manual) this stays fail-closed.

    Both containment checks are word-boundary matched (``_bounded_present``,
    2026-07-23 fix) — a bare substring search let short/ambiguous tokens
    ('ai', 'ml', 'css') false-match inside unrelated words in EITHER
    direction ('ai' ⊂ 'training'; a short denial 'ai' ⊂ 'maintenance').
    """
    token_norm = _norm(token)
    if not token_norm:
        return False
    token_forms = _alias_forms(token_norm)
    for d in denials:
        d_norm = _norm(d)
        if not d_norm:
            continue
        if any(_bounded_present(d, tf) for tf in token_forms):
            return True
        if _bounded_present(token, d_norm):
            if corpus is None or not _independently_affirmed(token, denials, corpus):
                return True
    return False


def is_denied_concept(
    concept: str, denials: list[str], corpus: str | None = None
) -> bool:
    """Public reuse of the denial predicate for #231's LEDGER-level override
    (services.keyword_ledger): does ``concept`` fall under any of the
    candidate's persisted ``denied_concepts`` tokens?

    Same alias-group + word-boundary + unicode-normalized machinery as
    ``enforce_stance`` (``_is_denied``) — one matching instrument for the
    same-turn op guard AND the durable ledger override, so the two can never
    disagree on what counts as denied. There is no INTERVIEW-TURN grounding
    corpus at ledger-build time (the denial was recorded turns ago), but the
    ledger builder MAY pass the vault's own literal text as ``corpus`` (#249
    run-4, 2026-07-24) — that lets the compound-containment rule's
    independent-affirmation check see real vault evidence instead of always
    fail-closing. Default ``None`` preserves the original fail-closed
    behaviour for any caller that has no vault text on hand.
    """
    return _is_denied(concept, denials, corpus)


def record_denials(
    metadata: ProfileMetadata,
    denials: list[str],
    *,
    statement: str,
    source: str,
    when: datetime | None = None,
    denial_level: Literal["direct", "partial"] = "direct",
) -> list[FieldChange]:
    """Persist explicit denials (#231) into ``metadata.denied_concepts`` and
    return the receipt ``FieldChange``s for a denial-only turn.

    The reconciler already drops a denied token from the SAME turn's ops
    (``enforce_stance``); this is the durable half — without it a denial-only
    answer ("no direct LegalTech experience, that's an honest gap") left no
    trace in the vault and a later ``analyze_gaps`` run could re-infer the
    denied concept via adjacency (F8).

    Deduplicated case-insensitively: re-denying the same concept refreshes its
    ``statement``/``date`` in place rather than appending a duplicate entry.
    Returns one ``FieldChange`` per NEW-or-refreshed denial so the caller can
    fold it into the turn's ``EnrichmentRecord`` receipt even when nothing
    else in the profile changed — a denial-only turn must not go silently
    unrecorded (#231a).

    ADR-064 — ``denial_level``: applies to every concept in ``denials`` for
    this call (a fresh concept is written at this level directly). On a
    RE-denial of an existing concept the level may only ever move
    ``direct -> partial``, never the reverse — a later, weaker probe (or a
    caller that simply didn't run the follow-up) must never erase that
    elicitation was already exhausted on an earlier turn. A re-denial at
    ``"direct"`` of a concept already at ``"partial"`` leaves it at
    ``"partial"``, and still refreshes ``statement``/``date`` like any other
    re-denial.
    """
    when = when or datetime.now()
    date_str = when.date().isoformat()
    changes: list[FieldChange] = []
    for raw in denials:
        text = raw.strip()
        if not text:
            continue
        norm = _norm(text)
        existing = next(
            (d for d in metadata.denied_concepts if _norm(d.concept) == norm), None
        )
        if existing is None:
            metadata.denied_concepts.append(
                DeniedConcept(
                    concept=text, statement=statement, source=source, date=date_str,
                    denial_level=denial_level,
                )
            )
            changes.append(
                FieldChange(
                    section="metadata",
                    field="denied_concepts",
                    action="added",
                    old_value=None,
                    new_value=text,
                    rationale=f"Noted limit: no hands-on {text} (candidate's own testimony)",
                )
            )
            continue

        # No-downgrade invariant (ADR-064): only ever move direct -> partial.
        level_upgraded = denial_level == "partial" and existing.denial_level == "direct"
        content_changed = existing.statement != statement or existing.source != source
        if not content_changed and not level_upgraded:
            continue

        if content_changed:
            existing.statement = statement
            existing.source = source
        existing.date = date_str
        if level_upgraded:
            existing.denial_level = "partial"
        rationale = (
            f"Escalated limit: no hands-on {text}, and adjacent coverage is "
            "also ruled out now (candidate's own testimony)"
            if level_upgraded
            else f"Re-confirmed limit: no hands-on {text} (candidate's own testimony)"
        )
        changes.append(
            FieldChange(
                section="metadata",
                field="denied_concepts",
                action="updated",
                old_value=text,
                new_value=text,
                rationale=rationale,
            )
        )
    return changes


def _text_claims_denied(text: str, denials: list[str]) -> bool:
    # Word-boundary matched (_bounded_present) for the same reason as _is_denied:
    # a bare substring check let a denial of "AI" drop truthful bullets whose only
    # "match" was inside "training"/"maintenance" (adversarial pass 2026-07-23).
    text_norm = _norm(text)
    return any(_bounded_present(d, text_norm) for d in denials)


def _raw_grounding_text(new_info: Any, source: str) -> str | None:
    """UN-normalised grounding text for interview turns; None otherwise.

    Grounding is an interview-turn instrument only (#127 scope decision): a CV
    import reconciles a whole staged extraction, where token presence is
    trivially satisfied and paraphrase is legitimate.

    Built-in interview: gap+question+answer — those fields are Applire-authored,
    so the question may legitimately ground a token the answer only affirms.
    Agent interview (E045 submit_claims): the ANSWER (the claimant's statement)
    only — all fields are claimant-authored, and an agent could otherwise
    smuggle a token through its own question (#127 class, adversarial B3).

    The raw (non-normalised) text is what ADR-061 clause 2's adjudication
    prompt shows the model and what ``_citation_verified`` checks the
    returned quote against — the candidate's own words, not the folded form.
    """
    if not isinstance(new_info, dict):
        return None
    if source == "interview":
        parts = [str(v) for v in new_info.values() if isinstance(v, str)]
        return " ".join(parts)
    if source == "agent_interview":
        answer = new_info.get("answer")
        return answer if isinstance(answer, str) else ""
    return None


def _grounding_corpus(new_info: Any, source: str) -> str | None:
    """Normalised grounding text for interview turns; None otherwise.

    Thin wrapper over :func:`_raw_grounding_text` — the raw text is the single
    source, normalised here ONLY, so the two can never drift apart.
    """
    raw = _raw_grounding_text(new_info, source)
    return _norm(raw) if raw is not None else None


_FIGURE_RE = re.compile(r"\d+(?:[.,]\d+)*")
# "2,000" / "2.000" / "1,000,000" — 3-digit separator groups read as thousands
# separators (EN comma, DE dot) as well as a decimal; both interpretations are
# kept ("1,000,000" must tokenize as ONE figure — finding O1).
_THOUSANDS_RE = re.compile(r"^\d{1,3}(?:[.,]\d{3})+$")


def _figure_variants(raw: str) -> set[str]:
    """Canonical readings of one digit group ("1,5" → 1.5; "2,000" → 2.000
    AND 2000)."""
    variants = {raw.replace(",", ".")}
    if _THOUSANDS_RE.match(raw):
        variants.add(raw.replace(",", "").replace(".", ""))
    return variants


# ── #207: spelled-out figures ("forty percent", "fünfundvierzig") ────────────
# Real testimony spells figures out; the rendered story op uses numerals. The
# grounding corpus therefore also yields the numeric value of EN/DE number
# words. "one"/"ein(e)" double as articles and stay EXCLUDED — parsing them
# would ground a fabricated "1" from almost any sentence (fail-closed).

_EN_UNITS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9,
}
_EN_TEENS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
_EN_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_DE_UNITS = {
    "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "fuenf": 5, "sechs": 6,
    "sieben": 7, "acht": 8, "neun": 9,
}
_DE_TEENS = {
    "zehn": 10, "elf": 11, "zwölf": 12, "zwoelf": 12, "dreizehn": 13,
    "vierzehn": 14, "fünfzehn": 15, "fuenfzehn": 15, "sechzehn": 16,
    "siebzehn": 17, "achtzehn": 18, "neunzehn": 19,
}
_DE_TENS = {
    "zwanzig": 20, "dreißig": 30, "dreissig": 30, "vierzig": 40,
    "fünfzig": 50, "fuenfzig": 50, "sechzig": 60, "siebzig": 70,
    "achtzig": 80, "neunzig": 90,
}
_SCALES = {
    "hundred": 100, "thousand": 1000, "million": 1_000_000,
    "billion": 1_000_000_000, "hundert": 100, "tausend": 1000,
    "millionen": 1_000_000, "milliarde": 1_000_000_000,
    "milliarden": 1_000_000_000,
}
_SMALL_WORDS = {**_EN_UNITS, **_EN_TEENS, **_EN_TENS, **_DE_UNITS, **_DE_TEENS, **_DE_TENS}
# German compounds are single words: "fünfundvierzig" (unit+und+tens),
# "zweihundert"/"zweitausend" (unit+scale). "ein" is unambiguous INSIDE a
# compound ("einundzwanzig", "einhundert") and allowed there.
_DE_COMPOUND_UNITS = {"ein": 1, **_DE_UNITS}
_DE_UNIT_ALT = "|".join(_DE_COMPOUND_UNITS)
_DE_UND_RE = re.compile(
    rf"^({_DE_UNIT_ALT})und({'|'.join(_DE_TENS)})(hundert|tausend)?$"
)
_DE_SCALE_RE = re.compile(rf"^({_DE_UNIT_ALT})?(hundert|tausend)$")

_WORD_RE = re.compile(r"[a-zäöüß]+")


def _spelled_figures(corpus_norm: str) -> set[str]:
    """Numeric values of spelled-out EN/DE number words in a normalised text."""
    values: set[int] = set()
    tokens = _WORD_RE.findall(corpus_norm)
    for i, tok in enumerate(tokens):
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        small = _SMALL_WORDS.get(tok)
        if small is not None:
            values.add(small)
            scale = _SCALES.get(nxt)
            if scale is not None:  # "two thousand"
                values.add(small * scale)
            unit = _EN_UNITS.get(nxt)
            if tok in _EN_TENS and unit is not None:  # "twenty five"
                pair = _EN_TENS[tok] + unit
                values.add(pair)
                nxt2 = tokens[i + 2] if i + 2 < len(tokens) else ""
                pair_scale = _SCALES.get(nxt2)
                if pair_scale is not None:  # "twenty-five thousand"
                    values.add(pair * pair_scale)
            continue
        if tok in _SCALES:
            values.add(_SCALES[tok])
            continue
        m = _DE_UND_RE.match(tok)
        if m:  # "fünfundvierzig", "fünfundzwanzigtausend"
            value = _DE_COMPOUND_UNITS[m.group(1)] + _DE_TENS[m.group(2)]
            if m.group(3):
                value *= _SCALES[m.group(3)]
            values.add(value)
            continue
        m = _DE_SCALE_RE.match(tok)
        if m:  # "zweihundert", "zweitausend"
            values.add(_DE_COMPOUND_UNITS.get(m.group(1) or "", 1) * _SCALES[m.group(2)])
    return {str(v) for v in values}


def _corpus_figure_set(corpus_norm: str) -> set[str]:
    """Every figure reading the turn grounds: digit groups (all canonical
    variants) plus spelled-out number words."""
    grounded: set[str] = set()
    for raw in _FIGURE_RE.findall(corpus_norm):
        grounded |= _figure_variants(raw)
    return grounded | _spelled_figures(corpus_norm)


def _story_keeps(
    op: UpsertStory, denials: list[str], corpus: str | None, raw_turn: str | None = None,
) -> bool:
    """US261 (ADR-055 gap): stories bypass token grounding — prose paraphrase
    is the notary's job — but figures and denials cannot be paraphrased into
    existence. `outcome`/`benchmark` figures become citable Oracle number
    provenance (ADR-052), so an ungrounded figure drops the WHOLE op (stripping
    it would silently editorialize the model's story; the caller can restate)."""
    prose = " ".join(
        p for p in (op.title, op.challenge, op.mechanism, op.outcome, op.benchmark) if p
    )
    if denials and _text_claims_denied(prose, denials):
        logger.warning(
            "reconcile stance: dropped story %r restating a denied token "
            "(US261/#127) — turn: %r", op.title, _log_turn(raw_turn),
        )
        return False
    if corpus is not None:
        grounded = _corpus_figure_set(corpus)
        ungrounded = sorted(
            {
                raw.replace(",", ".")
                for raw in _FIGURE_RE.findall(f"{op.outcome} {op.benchmark or ''}")
                if _figure_variants(raw).isdisjoint(grounded)
            }
        )
        if ungrounded:
            logger.warning(
                "reconcile stance: dropped story %r — outcome/benchmark "
                "figure(s) %s absent from the turn (Oracle number provenance, "
                "US261/ADR-052) — turn: %r", op.title, sorted(ungrounded),
                _log_turn(raw_turn),
            )
            return False
    return True


# ── ADR-061 clause 2 — the testimony predicate ────────────────────────────────
# Coverage ("is TOKEN present?") stays surface_present/_grounded, literal and
# unchanged (clause 1). Testimony ("did the candidate SAY this?") is this
# section: deterministic accept first (falls through to _grounded, so the
# overwhelming majority of ops never reach the LLM), then a narrow LLM
# adjudication of the uncertain band with a deterministically VERIFIED
# citation. The model does the semantics; code checks the quote.


def _citation_verified(quote: str, raw_turn: str) -> bool:
    """Is ``quote`` LITERALLY present in the turn's own text?

    Deliberately a bare, case-sensitive substring check — "literally present"
    (ADR-061 clause 2) — not a folded/normalised one: the whole point is to
    catch a model that answers "yes" with a plausible-but-fabricated quote
    (#306, the same run, is this codebase's live evidence that an LLM in a
    control path can go wrong). A genuine quote copied from the turn passes
    trivially; anything else — paraphrase, translation, a fixed typo, invented
    text — is rejected, and the caller falls back to ``unconfirmed``.
    """
    q = quote.strip()
    if not q:
        return False
    return q in raw_turn


async def _adjudicate_testimony(
    provider: "LLMProvider", token: str, kind: str, raw_turn: str,
) -> tuple[str, str] | None:
    """One narrow yes/no/unclear question to the LLM, with a citation.

    Returns ``(answer, quote)`` or ``None`` on ANY failure — provider outage,
    timeout, malformed JSON, or a response missing/mistyping the expected
    keys. ``None`` always means the caller falls back to ``unconfirmed``,
    never ``confirmed`` (ADR-061 clause 2's asymmetric failure handling) — this
    function never raises.
    """
    try:
        data = await provider.aparse_json(
            build_stance_adjudication_prompt(token, kind, raw_turn),
            system=STANCE_ADJUDICATION_SYSTEM_PROMPT,
            temperature=0.0,
            max_tokens=STANCE_ADJUDICATION_MAX_TOKENS,
        )
    except Exception:  # noqa: BLE001 — provider/transport/parse noise, never confirmed
        logger.warning(
            "reconcile stance: testimony adjudication call failed for %s %r "
            "— unconfirmed (ADR-061)", kind, token,
        )
        return None
    if not isinstance(data, dict):
        return None
    answer = data.get("answer")
    quote = data.get("quote")
    if answer not in ("yes", "no", "unclear") or not isinstance(quote, str):
        logger.warning(
            "reconcile stance: malformed testimony adjudication response for "
            "%s %r: %r — unconfirmed (ADR-061)", kind, token, data,
        )
        return None
    return answer, quote


async def _testimony_status(
    token: str,
    kind: str,
    corpus: str,
    raw_turn: str | None,
    provider: "LLMProvider | None",
) -> tuple[str, str | None]:
    """ADR-061 clause 2: resolve whether the candidate testified to ``token``.

    Returns ``("confirmed" | "unconfirmed", quote)``. Deterministic accept
    (``_grounded``) is tried first and costs nothing extra for the
    overwhelming majority of ops. Only the uncertain band reaches the LLM, and
    only a "yes" answer whose quote passes ``_citation_verified`` is trusted —
    every other outcome (no provider available, no turn text, "no", "unclear",
    an unverifiable quote, or a failed call) is ``unconfirmed``.
    """
    if _grounded(token, corpus):
        return "confirmed", None
    if provider is None or not raw_turn:
        return "unconfirmed", None
    verdict = await _adjudicate_testimony(provider, token, kind, raw_turn)
    if verdict is None:
        return "unconfirmed", None
    answer, quote = verdict
    if answer == "yes" and _citation_verified(quote, raw_turn):
        return "confirmed", quote
    return "unconfirmed", (quote.strip() or None)


async def _resolve_token(
    token: str,
    kind: str,
    *,
    denials: list[str],
    corpus: str | None,
    raw_turn: str | None,
    provider: "LLMProvider | None",
) -> str:
    """One token's full stance verdict: ``"denied"``, ``"confirmed"``, or
    ``"unconfirmed"``. Denial (ADR-040) is checked first and always wins —
    never-claim outranks claim regardless of what the testimony predicate
    would otherwise say. Non-interview sources (``corpus is None``) are
    exempt from grounding entirely (unchanged #127 scope decision — CV import
    reconciles a whole staged extraction, where paraphrase is legitimate)."""
    if denials and _is_denied(token, denials, corpus):
        return "denied"
    if corpus is None:
        return "confirmed"
    status, _quote = await _testimony_status(token, kind, corpus, raw_turn, provider)
    return status


async def enforce_stance(
    ops: list[ReconcileOp],
    *,
    denials: list[str],
    new_info: Any,
    source: str,
    provider: "LLMProvider | None" = None,
) -> list[ReconcileOp]:
    """Strip op content that contradicts the model's own denials; resolve
    interview-turn token claims via the ADR-061 testimony predicate.

    Scope: token-like claims (skill / technology / language / certification
    names) plus free-text bullets that restate a denied token, plus signature
    stories (denials over the prose; outcome/benchmark figure grounding —
    US261, closing the ADR-055 entity-upsert gap). Other entity upserts
    (work/project/volunteer) stay out of scope — they legitimately echo profile
    knowledge (target merges, alternate titles, rule 7).

    ``provider`` is optional: ``None`` (e.g. a caller with no LLM on hand)
    means every ungrounded token falls straight to ``unconfirmed`` without an
    adjudication attempt — a strict subset of the failure paths clause 2
    already requires, never ``confirmed``.

    Entity ops (UpsertSkill/UpsertLanguage/UpsertCertification) ALWAYS survive
    a non-denial verdict now (ADR-061 clause 3): the op is kept with
    ``status="confirmed"`` or ``status="unconfirmed"`` rather than dropped.
    ``AddBullets.technologies`` has no per-item status field to carry that on,
    so it keeps the pre-ADR-061 binary keep/drop — an unconfirmed technology
    tag is still dropped, but morphological misses now reach the same
    adjudication path first and are rescued exactly like a standalone skill.
    """
    corpus = _grounding_corpus(new_info, source)
    raw_turn = _raw_grounding_text(new_info, source)

    async def keep_token(token: str, kind: str) -> bool:
        """Binary gate for free-text token lists (AddBullets.technologies):
        denied -> dropped (ADR-040); unconfirmed -> dropped (no status slot to
        carry it on); confirmed -> kept."""
        verdict = await _resolve_token(
            token, kind, denials=denials, corpus=corpus, raw_turn=raw_turn,
            provider=provider,
        )
        if verdict == "denied":
            logger.warning(
                "reconcile stance: dropped DENIED %s %r (the model's own denial "
                "verdict outranks its ops, ADR-040/#127) — turn: %r",
                kind, token, _log_turn(raw_turn),
            )
            return False
        if verdict == "unconfirmed":
            logger.warning(
                "reconcile stance: dropped unconfirmed %s %r — the testimony "
                "predicate could not confirm the token, and free-text tags "
                "have no unconfirmed slot to carry it on (ADR-061) — turn: %r",
                kind, token, _log_turn(raw_turn),
            )
            return False
        return True

    async def entity_verdict(token: str, kind: str) -> tuple[bool, str]:
        """Three-way gate for UpsertSkill/UpsertLanguage/UpsertCertification:
        (keep, status). Only a denial drops the op; confirmed/unconfirmed are
        both kept (ADR-061 clause 3 — the guard stops deleting testimony)."""
        verdict = await _resolve_token(
            token, kind, denials=denials, corpus=corpus, raw_turn=raw_turn,
            provider=provider,
        )
        if verdict == "denied":
            logger.warning(
                "reconcile stance: dropped DENIED %s %r (the model's own denial "
                "verdict outranks its ops, ADR-040/#127) — turn: %r",
                kind, token, _log_turn(raw_turn),
            )
            return False, "confirmed"
        if verdict == "unconfirmed":
            logger.warning(
                "reconcile stance: %s %r written unconfirmed — the testimony "
                "predicate could not confirm the token; visible and "
                "candidate-confirmable, never claimable (ADR-061 clauses 2/3) "
                "— turn: %r", kind, token, _log_turn(raw_turn),
            )
        return True, verdict

    result: list[ReconcileOp] = []
    for op in ops:
        if isinstance(op, UpsertSkill):
            keep, status = await entity_verdict(op.name, "skill")
            if not keep:
                continue
            if status != op.status:
                op = op.model_copy(update={"status": status})
        elif isinstance(op, UpsertLanguage):
            keep, status = await entity_verdict(op.language, "language")
            if not keep:
                continue
            if status != op.status:
                op = op.model_copy(update={"status": status})
        elif isinstance(op, UpsertCertification):
            keep, status = await entity_verdict(op.name, "certification")
            if not keep:
                continue
            if status != op.status:
                op = op.model_copy(update={"status": status})
        elif isinstance(op, UpsertStory):
            if not _story_keeps(op, denials, corpus, raw_turn):
                continue
        elif isinstance(op, AddBullets):
            technologies = [t for t in op.technologies if await keep_token(t, "technology")]
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
                        "token: %r (#127) — turn: %r", b, _log_turn(raw_turn),
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


def exclude_unconfirmed(profile_json: dict[str, Any] | None) -> dict[str, Any]:
    """A shallow COPY of ``profile_json`` with every ``status="unconfirmed"``
    skill/language/certification entry removed (ADR-061 clause 3).

    Consumer-facing filter for anything that hands the vault to a document
    generator — an LLM prompt (the CV/cover-letter "CANDIDATE PROFILE" block)
    or a deterministic passthrough (``cv.py::_apply_certifications``) — an
    unconfirmed entry must never reach a generated document as though it were
    established fact: it cannot back a CV bullet or a letter sentence
    (ADR-061 clause 3). Never mutates the input and never touches the
    persisted profile — callers pass this filtered copy downstream only, so
    the candidate's own unconfirmed entries stay intact in the vault and
    remain visible/promotable via the profile UI.

    Deliberately narrow: only the three entity lists clause 3 names are
    filtered. Everything else (work/project history, denied_concepts,
    metadata) passes through untouched — this is not a general profile
    sanitiser, and the Keyword Ledger's own `direct`-status classification is
    a separate seam (#318, sequenced behind this clause).
    """
    if not isinstance(profile_json, dict):
        return {}
    out = dict(profile_json)
    for field in ("skills", "languages", "certifications"):
        entries = out.get(field)
        if isinstance(entries, list):
            out[field] = [
                e for e in entries
                if not (isinstance(e, dict) and e.get("status") == "unconfirmed")
            ]
    return out
