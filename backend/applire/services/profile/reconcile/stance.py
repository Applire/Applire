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
import re
from datetime import datetime
from typing import Any

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

logger = logging.getLogger(__name__)


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
    """
    token_norm = _norm(token)
    if not token_norm:
        return False
    token_forms = _alias_forms(token_norm)
    for d in denials:
        d_norm = _norm(d)
        if not d_norm:
            continue
        if any(surface_present(d, tf) for tf in token_forms):
            return True
        if surface_present(token, d_norm):
            if corpus is None or not _independently_affirmed(token, denials, corpus):
                return True
    return False


def is_denied_concept(concept: str, denials: list[str]) -> bool:
    """Public reuse of the denial predicate for #231's LEDGER-level override
    (services.keyword_ledger): does ``concept`` fall under any of the
    candidate's persisted ``denied_concepts`` tokens?

    Same alias-group + word-boundary + unicode-normalized machinery as
    ``enforce_stance`` (``_is_denied``) — one matching instrument for the
    same-turn op guard AND the durable ledger override, so the two can never
    disagree on what counts as denied. There is no interview-turn grounding
    corpus at ledger-build time (the denial was recorded turns ago), so this
    calls ``_is_denied`` with ``corpus=None`` — the same fail-closed behaviour
    ``enforce_stance`` already falls back to outside an interview turn.
    """
    return _is_denied(concept, denials, corpus=None)


def record_denials(
    metadata: ProfileMetadata,
    denials: list[str],
    *,
    statement: str,
    source: str,
    when: datetime | None = None,
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
                    concept=text, statement=statement, source=source, date=date_str
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
        elif existing.statement != statement or existing.source != source:
            existing.statement = statement
            existing.source = source
            existing.date = date_str
            changes.append(
                FieldChange(
                    section="metadata",
                    field="denied_concepts",
                    action="updated",
                    old_value=text,
                    new_value=text,
                    rationale=f"Re-confirmed limit: no hands-on {text} (candidate's own testimony)",
                )
            )
    return changes


def _text_claims_denied(text: str, denials: list[str]) -> bool:
    text_norm = _norm(text)
    return any(surface_present(d, text_norm) for d in denials)


def _grounding_corpus(new_info: Any, source: str) -> str | None:
    """Normalised grounding text for interview turns; None otherwise.

    Grounding is an interview-turn instrument only (#127 scope decision): a CV
    import reconciles a whole staged extraction, where token presence is
    trivially satisfied and paraphrase is legitimate.

    Built-in interview: gap+question+answer — those fields are Applire-authored,
    so the question may legitimately ground a token the answer only affirms.
    Agent interview (E045 submit_claims): the ANSWER (the claimant's statement)
    only — all fields are claimant-authored, and an agent could otherwise
    smuggle a token through its own question (#127 class, adversarial B3).
    """
    if not isinstance(new_info, dict):
        return None
    if source == "interview":
        parts = [str(v) for v in new_info.values() if isinstance(v, str)]
        return _norm(" ".join(parts))
    if source == "agent_interview":
        answer = new_info.get("answer")
        return _norm(answer) if isinstance(answer, str) else _norm("")
    return None


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


def _story_keeps(op: UpsertStory, denials: list[str], corpus: str | None) -> bool:
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
            "(US261/#127)", op.title,
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
                "US261/ADR-052)", op.title, sorted(ungrounded),
            )
            return False
    return True


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
    names) plus free-text bullets that restate a denied token, plus signature
    stories (denials over the prose; outcome/benchmark figure grounding —
    US261, closing the ADR-055 entity-upsert gap). Other entity upserts
    (work/project/volunteer) stay out of scope — they legitimately echo profile
    knowledge (target merges, alternate titles, rule 7).
    """
    corpus = _grounding_corpus(new_info, source)

    def keep_token(token: str, kind: str) -> bool:
        if denials and _is_denied(token, denials, corpus):
            logger.warning(
                "reconcile stance: dropped DENIED %s %r (the model's own denial "
                "verdict outranks its ops, ADR-040/#127)", kind, token,
            )
            return False
        if corpus is not None and not _grounded(token, corpus):
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
        elif isinstance(op, UpsertStory):
            if not _story_keeps(op, denials, corpus):
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
