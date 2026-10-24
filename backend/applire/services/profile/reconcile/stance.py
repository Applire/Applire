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
from applire.providers.llm.debug_log import llm_log_stage
from applire.prompts.stance_adjudication import (
    STANCE_ADJUDICATION_SYSTEM_PROMPT,
    build_stance_adjudication_prompt,
    render_interview_turn,
)
from applire.schemas.profile import (
    DeniedConcept,
    FieldChange,
    MasterProfileData,
    PendingConfirmation,
    ProfileMetadata,
)
from applire.services.ats_audit import _fold_variants, _norm, surface_present
from applire.services.profile.reconcile.ops import (
    AddBullets,
    DemoteSkill,
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


#: FS-7 (ADR-059 amended 2026-10-24) — the CLOSED list of generic head nouns
#: that make a denial declare its bare term. Lexical (ADR-062 cl. 1): a new
#: head noun is a code change with a test, never a runtime list. Forms are
#: ``_norm``-ed (lower case, hyphens folded to spaces).
_HEAD_NOUNS_TRAILING: tuple[str, ...] = (
    "experience", "experiences", "knowledge", "skills", "skill", "expertise",
    "background", "know how",
)
_HEAD_NOUNS_LEADING: tuple[str, ...] = (
    "erfahrung", "erfahrungen", "kenntnisse", "kenntnis", "wissen", "know how",
    "expertise", "experience", "knowledge", "background",
)
#: Only quantity/ownership words may precede a leading head noun; a NARROWING
#: qualifier ("praktische", "hands-on", "direkte", "formal") never strips.
_HEAD_FRAME_QUALIFIERS: tuple[str, ...] = ("eigene", "eigenen", "keine", "own", "no", "any")
_HEAD_FRAME_PREPOSITIONS: tuple[str, ...] = (
    "mit", "in", "im", "von", "zu", "zum", "zur", "with", "of", "on",
)
_TRAILING_HEAD_RE = re.compile(
    r"^(?P<term>.+?) (?:" + "|".join(re.escape(h) for h in _HEAD_NOUNS_TRAILING) + r")$"
)
_LEADING_HEAD_RE = re.compile(
    r"^(?:(?:" + "|".join(_HEAD_FRAME_QUALIFIERS) + r") )?"
    r"(?:" + "|".join(re.escape(h) for h in _HEAD_NOUNS_LEADING) + r") "
    r"(?:" + "|".join(_HEAD_FRAME_PREPOSITIONS) + r") (?P<term>.+)$"
)


def head_noun_term(denial: str) -> str | None:
    """FS-7 — the bare term a denial phrased as term + generic head noun
    declares, or ``None`` (ADR-059 amended 2026-10-24).

    "payment-institution experience" → "payment institution";
    "eigene Erfahrung mit Vertrieb" → "vertrieb"; "audit-trail background" →
    "audit trail". Only when the denial is EXACTLY such a frame; a narrowing
    qualifier stays in the term ("formal SBAR experience" → "formal sbar", never
    bare "sbar"), and a denial that IS a head noun declares nothing. Returned
    ``_norm``-ed. Pure.
    """
    d = _norm(denial or "")
    if not d:
        return None
    for rx in (_LEADING_HEAD_RE, _TRAILING_HEAD_RE):
        m = rx.match(d)
        if m:
            term = m.group("term").strip()
            if term and term not in _HEAD_NOUNS_TRAILING and term not in _HEAD_NOUNS_LEADING:
                return term
    return None


def _declared_forms(denial: str) -> tuple[str, ...]:
    """The forms a denial DECLARES: itself, plus its FS-7 bare term."""
    bare = head_noun_term(denial)
    return (denial, bare) if bare else (denial,)


def _declares(denial: str, token_forms: frozenset[str]) -> bool:
    """The DECLARED branch, alone: the denied term itself names the token (or a
    broader term the token falls under — "Azure" declares "Microsoft Azure",
    "Kubernetes" declares "K8s" via the token's alias form).

    FS-7 (ADR-059 amended 2026-10-24): a denial phrased as term + generic head
    noun ("payment-institution experience", "eigene Erfahrung mit Vertrieb")
    also declares its bare term (:func:`head_noun_term`) — a lexical fact, so
    the bare term is floored absolutely instead of being read as a narrower
    compound that the candidate's CV could release.

    Never the compound-containment branch below. See
    :func:`declared_denial_matches` for why the two are separable.
    """
    return any(_bounded_present(form, tf) for form in _declared_forms(denial) for tf in token_forms)


def declared_only_by_head_noun(token: str, denials: list[str]) -> list[str]:
    """FS-7 — the denials that declare ``token`` ONLY through their head-noun
    frame (the literal denial does not name it). These are the matches whose
    floor a CV naming the term would have released before 2026-10-24: the
    contradiction the Health question asks about. Longest first. Pure."""
    token_norm = _norm(token)
    if not token_norm:
        return []
    forms = _alias_forms(token_norm)
    out = [
        d for d in denials
        if _norm(d)
        and not any(_bounded_present(d, tf) for tf in forms)
        and _declares(d, forms)
    ]
    return sorted(out, key=lambda d: len(_norm(d)), reverse=True)


def declared_denial_matches(token: str, denials: list[str]) -> list[str]:
    """The denied terms that DECLARE ``token`` — longest first, never the
    compound-containment branch (ADR-059 amended 2026-08-08, #486).

    ``is_denied_concept`` answers ONE question ("may this concept be claimed?")
    with two branches of opposite polarity, and the ledger floor was using it
    for two different acts:

    * **refusing a claim** — a false positive claims LESS (ADR-062 clause 5's
      sanctioned direction), so containment is the right instrument and keeps
      the whole predicate;
    * **asserting a denial** — writing ``status="denied"`` +
      ``DENIED_EVIDENCE`` is *testimony*, a statement that the candidate said
      they lack this. A declared "Tailwind CSS" is no statement whatsoever
      about bare "CSS", so asserting one fabricates testimony.

    This function is the assert half's matcher: the declared term must itself
    name the token. Matching is the SAME machinery as the full predicate
    (unicode-folded ``_norm``, word-boundary ``_bounded_present``, the ``#207``
    alias groups) — one instrument, one branch dropped, never a second matcher
    that could quietly disagree with the never-upgrade half about what a denial
    even is.

    Longest first (the ``#451``/ADR-064 subterm-scoping precedent) so a caller
    that must pick ONE matching denial picks the most specific one. No corpus
    parameter: the declared branch is corpus-independent by construction —
    ``_independently_affirmed`` exists only to ground the containment reading.
    """
    token_norm = _norm(token)
    if not token_norm:
        return []
    token_forms = _alias_forms(token_norm)
    matches = [d for d in denials if _norm(d) and _declares(d, token_forms)]
    return sorted(matches, key=lambda d: len(_norm(d)), reverse=True)


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

    The first branch is :func:`_declares` — shared verbatim with
    :func:`declared_denial_matches`, so the never-upgrade half and the assert
    half can never disagree about what "the candidate declared this" means.
    """
    token_norm = _norm(token)
    if not token_norm:
        return False
    token_forms = _alias_forms(token_norm)
    for d in denials:
        d_norm = _norm(d)
        if not d_norm:
            continue
        if _declares(d, token_forms):
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


def containment_release_form(
    concept: str,
    forms: list[str] | None,
    denials: list[str],
    corpus: str | None,
) -> str | None:
    """The ledger entry's OWN name that RELEASES a containment-only denial
    match — or ``None`` when nothing releases it (ADR-059 amended 2026-09-11,
    SF-GAP.10 / E-4).

    ``_independently_affirmed`` is the release half of the compound-containment
    branch: "CSS" is floored by a denied "Tailwind CSS" only while the vault
    never affirms "CSS" outside that compound. Until now the floor asked that
    question about the entry's ``concept`` **alone**, while asking the DENIAL
    question about the concept *and* every ``surface_forms`` entry
    (:func:`applire.services.keyword_ledger._entry_is_denied`). The two widths
    were asymmetric, and the asymmetry always fails in the same direction:
    every alias can pull an entry INTO the floor and none can carry it out.

    The 2026-09-10 delivery run is the instance. The candidate denied
    ``"direkte Produktion für Lebensmittelkunden"``; the JD's bare requirement
    ``Produktion`` (surface forms ``Produktion``/``Produktionsleiter``/
    ``Fertigung``) is a whole word strictly inside that compound, so the
    containment branch fired. Nothing released it: the German compound
    ``produktionsleiter`` — an attested ``work_experience[].role``, sitting in
    :func:`denial_release_corpus` — is not the token ``produktion`` at a word
    boundary. A met, ``direct``, narrative-backed requirement was floored into
    ``category_c`` and shipped to the user as a critical gap while the same
    response still listed it under ``strengths``. Reconcile rule 9's principle
    ("A DENIAL IS ABOUT ITS OWN ITEM AND NOTHING ELSE") is the one being
    restored here: a narrow denial about producing *for food customers* says
    nothing about production as such.

    So the release witness is the entry's own identity — ``concept`` plus every
    ``surface_forms`` entry — judged with the SAME instrument
    (:func:`_independently_affirmed`: unicode-folded, whole-token, denials
    blanked out of the corpus first). Never a substring reading of the corpus,
    and never a widening of what counts as affirmation: the corpus stays
    :func:`denial_release_corpus`'s attested entity labels (#480 §7.5(a)).

    A **declared** denial is absolute and is checked here, not left to the call
    site: if any probe is named by a denial (:func:`declared_denial_matches`)
    the function returns ``None`` however loudly the vault affirms it — ADR-040
    never-claim-beats-claim, the candidate's own statement about their own
    term. The release therefore reaches exactly the containment-only case the
    branch was fail-closed for.

    Returns the releasing form (for the log line and the seam tests), or
    ``None``. Pure; tolerant of ``None``/empty on every argument.
    """
    probes = [
        p
        for p in dict.fromkeys([concept, *(forms or [])])
        if isinstance(p, str) and p.strip()
    ]
    if not probes or not corpus or not denials:
        return None
    if any(declared_denial_matches(p, denials) for p in probes):
        return None
    for probe in probes:
        if _independently_affirmed(probe, denials, corpus):
            return probe
    return None


def denials_covering(
    token: str,
    forms: list[str] | None,
    denials: list[str],
    corpus: str | None,
) -> list[str]:
    """The recorded denials that cover ``token`` — the ledger floor's question,
    asked of a term a DOCUMENT carries (build-2 delivery F2, SF-REVIEW.18).

    The review surface offers *Stimmt, in mein Profil aufnehmen* on a term the
    profile does not back. When one of the candidate's own recorded denials
    covers that term, the offer invites them to write the claim they disclaimed
    into their profile ("Lebensmittelkunden" under the denial "direkte
    Produktion für Lebensmittelkunden"). This answers "which denial covers it"
    with the floor's OWN machinery, never a second matcher (ADR-066): the term
    is covered when :func:`is_denied_concept` holds for it or any of its forms
    over the JOINT denial list, and :func:`containment_release_form` does not
    release it against ``corpus`` (:func:`denial_release_corpus`). A fact, not
    a judgement about the sentence (ADR-062 cl. 1): the term may stand in an
    honest negation; what changes is only that the profile write is not
    offered. Returns the covering denials longest first (declared ones and the
    compounds that contain a probe), ``[]`` when none covers it.
    """
    probes = [
        p for p in dict.fromkeys([token, *(forms or [])])
        if isinstance(p, str) and p.strip()
    ]
    denials = [d for d in denials or [] if isinstance(d, str) and d.strip()]
    if not probes or not denials:
        return []
    if not any(is_denied_concept(p, denials, corpus) for p in probes):
        return []
    if containment_release_form(token, forms, denials, corpus) is not None:
        return []
    covering = []
    for d in denials:
        d_norm = _norm(d)
        if any(
            _declares(d, _alias_forms(_norm(p))) or _bounded_present(p, d_norm)
            for p in probes
        ):
            covering.append(d)
    return sorted(dict.fromkeys(covering), key=lambda d: len(_norm(d)), reverse=True)


def record_denials(
    metadata: ProfileMetadata,
    denials: list[str],
    *,
    statement: str,
    source: str,
    when: datetime | None = None,
    denial_level: Literal["direct", "partial"] = "direct",
    level_only: bool = False,
) -> list[FieldChange]:
    """Persist explicit denials (#231) into ``metadata.denied_concepts`` and
    return the receipt ``FieldChange``s for a denial-only turn.

    The reconciler already drops a denied token from the SAME turn's ops
    (``enforce_stance``); this is the durable half — without it a denial-only
    answer ("no direct LegalTech experience, that's an honest gap") left no
    trace in the vault and a later ``analyze_gaps`` run could re-infer the
    denied concept via adjacency (F8).

    Deduplicated case-insensitively: re-denying the same concept refreshes its
    existing record in place rather than appending a duplicate entry.
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
    ``"partial"``, and still refreshes ``date`` like any other re-denial.

    **``statement``/``source`` are WRITE-ONCE (#348).** They are the
    candidate's verbatim testimony, and only the concept's OWN first denial
    may set them. A re-denial — of the same concept or of anything else in the
    same call — refreshes ``date`` and may upgrade ``denial_level``, and
    touches nothing else.

    The defect this closes: on the ADR-064 charter run (2026-07-29) a turn-6
    reconcile spuriously re-emitted a turn-5 concept among its own ``denials``,
    and the in-place refresh filed the turn-6 ISO-45001 sentence under the
    turn-5 concept "formale Investitionsplanung". The record stayed
    schema-valid, so nothing surfaced it — and ``DeniedConcept.statement`` is a
    citable vault path for the Oracle (``oracle/matchers/vault.py``), the
    letter's STATED LIMIT block (``cross_document.collect_stated_limits``) and
    the transfer bridge (``cover_letter_positioning``), so the wrong words then
    propagated into generated documents.

    Why write-once rather than "validate that the new statement concerns the
    concept": deciding whether a sentence is *about* a concept is a judgement,
    and ADR-062 clause 1 forbids deterministic code from making one. Immutable
    testimony needs no judgement. This also makes the ordinary path agree with
    the ``level_only`` path below, which has treated ``statement`` as immutable
    since the ADR-064 F1 finding-fix, and with ADR-059's 2026-07-26 amendment
    clause 4 ("a correction is a new fact about the record, not a rewrite of
    it") — the amendment's explicit candidate-correction path is the ONLY
    sanctioned way a denial record's content changes, and it is receipted
    rather than overwritten. ADR-059's original clause 1 wording ("re-denial
    refreshes in place") is therefore read as *the record* is refreshed in
    place — no duplicate entry — not *the testimony* is overwritten.

    ``level_only`` (F1 finding-fix, 2026-07-29): the ADR-064 transfer-probe
    escalation (a SECOND denial of the SAME probed concept bumps its durable
    level to ``"partial"``) must never rewrite ``statement`` — the level is
    elicitation bookkeeping, not testimony content, and the candidate's
    verbatim words must stay immutable once written. When ``True``:
      * only an EXISTING entry (matched by ``_norm``) is touched — there is
        nothing to escalate FROM for a concept with no durable record, so a
        not-found concept is a no-op (never mints a fresh entry as if it were
        attested testimony);
      * ``statement``/``source`` are NEVER written, regardless of whether
        they differ from the existing record;
      * only an actual ``direct -> partial`` upgrade produces a change (a
        level-only call against an already-``"partial"`` or nonexistent
        concept is a no-op, not a silent no-change "success").
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
            if level_only:
                # Nothing durable to escalate — fail safe rather than minting
                # testimony the candidate never gave (mirrors _mark_probe_asked).
                continue
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
                    # #669 / vault collector #602 — this sentence is shown
                    # verbatim inside the German "Was wir aus deinen Antworten
                    # übernommen haben" summary. It is not a confirmation, so
                    # `option_keys` cannot reach it; `rationale_key` is the
                    # instrument ADR-038 already built for exactly this, and the
                    # concept itself rides `new_value`, so the localized sentence
                    # needs no interpolation.
                    rationale_key="denial_noted",
                )
            )
            continue

        # No-downgrade invariant (ADR-064): only ever move direct -> partial.
        level_upgraded = denial_level == "partial" and existing.denial_level == "direct"

        if level_only:
            if not level_upgraded:
                continue  # nothing to escalate — never touch statement/source/date
            existing.denial_level = "partial"
            existing.date = date_str
            changes.append(
                FieldChange(
                    section="metadata",
                    field="denied_concepts",
                    action="updated",
                    old_value=text,
                    new_value=text,
                    rationale=(
                        f"Escalated limit: no hands-on {text}, and adjacent "
                        "coverage is also ruled out now (candidate's own "
                        "testimony) — level only, statement unchanged"
                    ),
                )
            )
            continue

        # #348 — `statement`/`source` are WRITE-ONCE. A re-denial refreshes the
        # record (date, and a direct -> partial level move); it never rewrites
        # the testimony. See the ADR-059 note in this function's docstring.
        restated = existing.statement != statement or existing.source != source
        if not restated and not level_upgraded:
            continue

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


def _testimony_texts(new_info: Any, source: str) -> tuple[str, str] | None:
    """``(answer, adjudication_turn)`` for the testimony predicate; None
    outside the interview doors.

    ADR-061 amended 2026-10-18 (ruling DEL-5 (1)): testimony is the
    candidate's ANSWER. On the built-in interview the gap label and the
    question are Applire's words — they may give the adjudicator CONTEXT for
    an answer that only says yes ("Ja, täglich"), but they can no longer
    ground a token on their own. Delivery run 2: "Bei Investitionen war ich
    beim MES-Projekt 2023 beteiligt" became a ``confirmed`` skill
    "Investitionsplanung" because the QUESTION named it, and reached both
    documents as an ownership claim. The deterministic accept therefore reads
    the answer only, the adjudicator sees the turn split
    (``render_interview_turn``), and the citation is verified against the
    answer alone. The agent door was already answer-only (B3) and is
    unchanged. ADR-061 amended 2026-10-20 (783:L97) moved the two leftovers
    the 2026-10-18 amendment named — the denial floor's independent-affirmation
    release and the story figure grounding — onto the same answer half
    (``enforce_stance``). Only the employer-anchor guard
    (``attribution.enforce_attribution``) keeps the whole-turn corpus of
    :func:`_grounding_corpus`: it reads the one sentence that owns a bullet,
    and a question sentence owns none.
    """
    if not isinstance(new_info, dict) or source not in ("interview", "agent_interview"):
        return None
    answer = new_info.get("answer")
    answer = answer if isinstance(answer, str) else ""
    if source == "agent_interview":
        return answer, answer
    gap = new_info.get("gap")
    question = new_info.get("question")
    turn = render_interview_turn(
        gap=gap if isinstance(gap, str) else None,
        question=question if isinstance(question, str) else None,
        answer=answer,
    )
    return answer, turn


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
        with llm_log_stage("stance_adjudication"):
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
    citation_text: str | None = None,
) -> tuple[str, str | None]:
    """ADR-061 clause 2: resolve whether the candidate testified to ``token``.

    Returns ``("confirmed" | "unconfirmed", quote)``. Deterministic accept
    (``_grounded``) is tried first and costs nothing extra for the
    overwhelming majority of ops. Only the uncertain band reaches the LLM, and
    only a "yes" answer whose quote passes ``_citation_verified`` is trusted —
    every other outcome (no provider available, no turn text, "no", "unclear",
    an unverifiable quote, or a failed call) is ``unconfirmed``.

    ``citation_text`` (default: ``raw_turn``) is the text the quote must come
    from — the ANSWER on the built-in interview (ADR-061 amended 2026-10-18),
    so a "yes" that quotes Applire's own question is rejected.
    """
    if _grounded(token, corpus):
        return "confirmed", None
    if provider is None or not raw_turn:
        return "unconfirmed", None
    verdict = await _adjudicate_testimony(provider, token, kind, raw_turn)
    if verdict is None:
        return "unconfirmed", None
    answer, quote = verdict
    if answer == "yes" and _citation_verified(quote, citation_text or raw_turn):
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
    testimony: tuple[str, str] | None = None,
) -> str:
    """One token's full stance verdict: ``"denied"``, ``"confirmed"``, or
    ``"unconfirmed"``. Denial (ADR-040) is checked first and always wins —
    never-claim outranks claim regardless of what the testimony predicate
    would otherwise say. Non-interview sources (``corpus is None``) are
    exempt from grounding entirely (unchanged #127 scope decision — CV import
    reconciles a whole staged extraction, where paraphrase is legitimate)."""
    # ADR-061 amended 2026-10-20 (783:L97, the DEL-5 sibling): the denial
    # floor's independent-affirmation release reads the ANSWER on the
    # interview doors — a question that names the part ("Haben Sie mit CSS
    # gearbeitet?") is Applire's text and may not release the candidate's
    # containment denial ("Tailwind CSS"). Stricter corpus → claims less
    # (ADR-062 cl. 5's sanctioned direction).
    denial_corpus = _norm(testimony[0]) if testimony is not None else corpus
    if denials and _is_denied(token, denials, denial_corpus):
        return "denied"
    if corpus is None:
        return "confirmed"
    if testimony is None:
        status, _quote = await _testimony_status(token, kind, corpus, raw_turn, provider)
        return status
    # ADR-061 amended 2026-10-18 (DEL-5 (1)): accept on the ANSWER, adjudicate
    # over the split turn, cite from the ANSWER.
    answer, turn = testimony
    status, _quote = await _testimony_status(
        token, kind, _norm(answer), turn, provider, citation_text=answer,
    )
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
    testimony = _testimony_texts(new_info, source)

    async def keep_token(token: str, kind: str) -> bool:
        """Binary gate for free-text token lists (AddBullets.technologies):
        denied -> dropped (ADR-040); unconfirmed -> dropped (no status slot to
        carry it on); confirmed -> kept."""
        verdict = await _resolve_token(
            token, kind, denials=denials, corpus=corpus, raw_turn=raw_turn,
            provider=provider, testimony=testimony,
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
            provider=provider, testimony=testimony,
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
            # ADR-061 amended 2026-10-20 (783:L97): a story's figures are
            # grounded in the candidate's ANSWER, never in a figure only the
            # gap label or the question states.
            story_corpus = _norm(testimony[0]) if testimony is not None else corpus
            if not _story_keeps(op, denials, story_corpus, raw_turn):
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


# THE set of vault statuses that back nothing. `unconfirmed` — the reconciler's
# own inference, never the candidate's testimony (ADR-061 clause 3). `denied` —
# the candidate RETRACTED it (ADR-061 amended 2026-08-08, #485). One tuple, so
# "which statuses may not reach a claim surface" is answered in one place and a
# future member cannot land at four of five sites.
_UNCLAIMABLE_STATUSES = frozenset({"unconfirmed", "denied"})


def entry_is_claimable(entry: Any) -> bool:
    """Can this raw vault entry (a skill/language/certification dict, or a bare
    legacy string) back an outbound claim?

    THE literal status check — :func:`exclude_unconfirmed`,
    :func:`claimable_skill_names` and every ad hoc filter that used to inline
    ``entry.get("status") == "unconfirmed"`` call this instead. A non-dict
    entry (legacy JSONB stored skills as bare strings) carries no status and is
    claimable by default, matching the pre-status behaviour.
    """
    if not isinstance(entry, dict):
        return True
    return entry.get("status") not in _UNCLAIMABLE_STATUSES


def claimable_skill_names(profile_json: dict[str, Any] | None) -> list[str]:
    """The vault's claimable skill NAMES, in profile order, verbatim.

    ADR-061 amendment 2026-08-08 clause 2: five helpers inside ``services/cv.py``
    each open-coded "iterate ``profile_json['skills']``, skip the unclaimable
    ones, take the name" with their own copy of the status literal, bypassing
    the shared predicate. They are consolidated here — the ADR's "one place"
    is made true rather than assumed. Callers keep their own downstream logic
    (dedupe, grouping, near-dupe matching); only the *pool* is shared.

    Tolerates ``None``/malformed input and legacy bare-string skill entries.
    Never mutates the input.
    """
    names: list[str] = []
    for entry in (profile_json or {}).get("skills") or []:
        if not entry_is_claimable(entry):
            continue
        name = entry.get("name") if isinstance(entry, dict) else entry
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return names


def exclude_unconfirmed(profile_json: dict[str, Any] | None) -> dict[str, Any]:
    """A shallow COPY of ``profile_json`` with every UNCLAIMABLE
    skill/language/certification entry removed — ``status="unconfirmed"``
    (ADR-061 clause 3) and ``status="denied"`` (ADR-061 amended 2026-08-08,
    #485).

    Consumer-facing filter for anything that hands the vault to a document
    generator — an LLM prompt (the CV/cover-letter "CANDIDATE PROFILE" block)
    or a deterministic passthrough (``cv.py::_apply_certifications``) — an
    unconfirmed entry must never reach a generated document as though it were
    established fact: it cannot back a CV bullet or a letter sentence
    (ADR-061 clause 3). Never mutates the input and never touches the
    persisted profile — callers pass this filtered copy downstream only, so
    the candidate's own unconfirmed entries stay intact in the vault and
    remain visible/promotable via the profile UI.

    **The name is kept deliberately.** It reads narrower than it is now, but a
    rename ripples through ten-plus call sites across three services for no
    behavioural gain, and the ADR amendment itself names this function as the
    one that "extends to ``denied`` in one edit". The honest statement of what
    it excludes is :data:`_UNCLAIMABLE_STATUSES`; this docstring, and
    :func:`entry_is_claimable`, are where a reader finds it.

    A ``denied`` entry additionally composes with #480 step 1: every corpus
    feeding the ADR-059 denial floor is built through this filter, so a
    retracted skill can never become the independent affirmation that releases
    a persisted denial.

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
            out[field] = [e for e in entries if entry_is_claimable(e)]
    return out


#: What separates two entity labels in :func:`denial_release_corpus`. A bare
#: space would let two unrelated entities read as one phrase ("Machine" +
#: "Learning" → "machine learning"), manufacturing an affirmation nobody
#: attested. A pipe survives ``_norm`` and is a word boundary to every presence
#: predicate, so no match can span two entities. Fail-closed, the ADR-062
#: clause 5 direction.
_LABEL_SEPARATOR = " | "


def denial_release_corpus(profile_json: dict[str, Any] | None) -> str:
    """The corpus that may RELEASE a persisted denial — **attested entity
    labels only** (ADR-059 amended 2026-08-09, #480 §7.5 option (a)).

    ``_independently_affirmed`` decides whether a broad concept is affirmed
    OUTSIDE every denied compound; whatever text it is handed is, in effect,
    what the system will accept as the candidate saying "yes I do have this"
    against their own recorded "no". Until now that text was
    :func:`applire.services.keyword_ledger.profile_literal_corpus` — the whole
    vault flattened, *including* ``work_experience[].responsibilities`` — so a
    sentence typed into the CV section editor silently lifted the candidate's
    own denial (#480's second probe half). Rationale for the narrowing, fact-only
    per ADR-062 clause 1: an affirmation strong enough to narrow a persisted
    denial must be a vault ENTITY the candidate attested, not a sentence typed
    into a document.

    **In:**

    * ``skills[].name`` where the entry is claimable — via the shared
      :func:`entry_is_claimable` predicate, so :data:`_UNCLAIMABLE_STATUSES`
      excludes ``unconfirmed`` **and** ``denied`` in one place and #480 step 1's
      ``exclude_unconfirmed`` wrap is subsumed by construction;
    * ``certifications[].name`` and ``languages[].language``, gated on
      ``status == "confirmed"`` — both carry a ``confirmed|unconfirmed``
      literal, and an unconfirmed certification is not attested. An entry with
      no ``status`` key reads as ``confirmed`` (the schema default, which is
      what legacy JSONB means); any OTHER value is excluded — fail-closed, and
      the only way this gate differs from :func:`entry_is_claimable`;
    * ``work_experience[].role``, ``.company`` and ``.technologies[]``.

    * **imported prose** (ADR-059 amended 2026-10-22, E-11 / ruling G-2) — the
      ``responsibilities``/``achievements``/``description`` units of roles,
      projects and volunteer activities that the candidate's OWN imported
      document supplied (:func:`imported_prose_units`). A bullet an interview
      turn, an agent or the CV section editor wrote never releases (#480's
      vector stays closed: the ``manual_edit`` receipt names the typed text).

    **Out, deliberately:** interview- and editor-written prose, signature
    stories, summaries and education free text. (The design's
    ``skills[].specialisation`` is dropped: the field does not exist in the
    schema.)

    **Why ``technologies[]`` is in** (PO addendum to the ADR-059 2026-08-09
    amendment, ruled the same day): it is a STRUCTURED TAG LIST, not a sentence
    — a vault entity label of the same trust level as ``role``/``company``, and
    the line this corpus draws is entity-vs-prose, not "is it on the skills
    list". Excluding it silently narrowed the charter-run-4 guarantee #249
    pinned (a broad "RAG" tied to the vault through ``technologies[]``, outside
    a narrow "RAG pipeline" denial), re-opening a slice of the #207
    over-blocking class this predicate exists to prevent.

    **Honest caveat, recorded with the amendment:** ``WorkEntry`` has **no
    status field**, so ``role``, ``company`` and ``technologies[]`` are only as
    attested as the reviewed import that wrote them — the weakest members of
    this corpus. They stay in per the PO ruling (dropping them would over-floor
    genuine role-title and technology-tag affirmations), and charter-run
    evidence of a misparse-driven release is grounds to narrow further.

    Normalised with the SAME ``ats_audit._norm`` every other presence corpus
    uses, or the matcher and the corpus would disagree about what a token is.
    Pure, ``None``/malformed tolerant; never mutates the input.

    This function is used at **every** site feeding the floor/release predicate
    and nowhere else. :func:`profile_literal_corpus` and its three coverage/diff
    consumers (the Oracle and cover-letter ``present_unsupported`` checks, the
    US147 pre-download diff) keep the wide corpus: they answer coverage
    questions, not release questions.
    """
    if not isinstance(profile_json, dict):
        return ""
    return _norm(_LABEL_SEPARATOR.join(denial_release_units(profile_json)))


def denial_release_units(profile_json: dict[str, Any] | None) -> list[str]:
    """The un-normalised units :func:`denial_release_corpus` is built from — one
    attested label or imported bullet each, in vault order. Exists so a reader
    that must SHOW the unit (the FS-7 Health question) quotes the vault's own
    wording; the release predicate reads only the joined corpus. Pure."""
    if not isinstance(profile_json, dict):
        return []

    labels: list[str] = []

    def _add(value: Any) -> None:
        if isinstance(value, str) and value.strip():
            labels.append(value.strip())

    for entry in profile_json.get("skills") or []:
        if not entry_is_claimable(entry):
            continue
        _add(entry.get("name") if isinstance(entry, dict) else entry)

    for field_name, label_key in (
        ("certifications", "name"),
        ("languages", "language"),
    ):
        for entry in profile_json.get(field_name) or []:
            if not isinstance(entry, dict):
                continue
            if entry.get("status", "confirmed") != "confirmed":
                continue
            _add(entry.get(label_key))

    for entry in profile_json.get("work_experience") or []:
        if not isinstance(entry, dict):
            continue
        _add(entry.get("role"))
        _add(entry.get("company"))
        for tech in entry.get("technologies") or []:
            _add(tech)

    # ADR-059 amended 2026-10-22 (E-11, Hazelnut build 3 / WP-G) — the candidate's
    # IMPORTED prose releases too. One unit per bullet, each behind the label
    # separator, so no match spans two bullets.
    for unit in imported_prose_units(profile_json):
        _add(unit)

    return labels


def _persisted_denials(profile_json: dict[str, Any]) -> list[str]:
    metadata = profile_json.get("metadata") or {}
    raw = metadata.get("denied_concepts") if isinstance(metadata, dict) else None
    denials = [
        d.get("concept") if isinstance(d, dict) else getattr(d, "concept", None)
        for d in raw or []
    ]
    return [d for d in denials if isinstance(d, str) and d.strip()]


def denial_cv_conflicts(profile_json: dict[str, Any] | None) -> list[dict[str, str]]:
    """Every persisted denial the candidate's own release corpus
    (:func:`denial_release_units`: attested labels + imported bullets) names.

    Two kinds, one record each per denial —
    ``{"kind", "denial_concept", "bare_term", "cv_label"}``:

    * ``"head_noun"`` (FS-7, ADR-059 amended 2026-10-24): the denial declares
      ``bare_term`` only through its generic head noun ("payment-institution
      experience" → "payment institution"), and a unit names that term OUTSIDE
      every denial wording.
    * ``"literal"``: a unit names the denied term itself at a word boundary
      ("Vertrieb" denied, a bullet "Vertrieb von …"). ``bare_term`` is the
      ``_norm``-ed denial.

    ``cv_label`` is the FIRST such unit, in the vault's own wording. The floor
    holds either way; this only finds the contradiction a Health item asks
    about. Word-boundary matched, so a German compound ("Vertriebserfahrung")
    is not found — recorded residue. Pure, ``None``/malformed tolerant.
    """
    if not isinstance(profile_json, dict):
        return []
    denials = _persisted_denials(profile_json)
    if not denials:
        return []
    units = denial_release_units(profile_json)
    out: list[dict[str, str]] = []
    for denial in denials:
        bare = head_noun_term(denial)
        if bare:
            kind = "head_noun"
            label = next(
                (u for u in units if _independently_affirmed(bare, denials, _norm(u))),
                None,
            )
        else:
            kind, bare = "literal", _norm(denial)
            label = next((u for u in units if _bounded_present(denial, _norm(u))), None)
        if label is not None:
            out.append(
                {"kind": kind, "denial_concept": denial, "bare_term": bare, "cv_label": label}
            )
    return out


def head_noun_scope_conflicts(profile_json: dict[str, Any] | None) -> list[dict[str, str]]:
    """FS-7 — the ``"head_noun"`` records of :func:`denial_cv_conflicts`, without
    the ``kind`` key: the denials that declare a bare term ONLY through their
    generic head noun while the candidate's own CV names that term. Pure."""
    return [
        {k: v for k, v in hit.items() if k != "kind"}
        for hit in denial_cv_conflicts(profile_json)
        if hit["kind"] == "head_noun"
    ]


#: ``EnrichmentRecord.source`` values whose prose is the candidate's OWN document
#: (ADR-059 amended 2026-10-22, E-11). Everything else is interview- or
#: editor-written and never releases a denial; an unknown source is treated as
#: not-an-import (fail-closed).
_IMPORT_SOURCES = frozenset({"cv_upload", "cv_paste", "linkedin_import", "xing_import"})

#: Prose sections whose units are release candidates — the bullets of a role,
#: project or volunteer activity. Signature stories (interview material by
#: construction) and the professional summary (may be system-seeded) are out.
_PROSE_SECTIONS = ("work_experience", "projects", "volunteer_activities")
_PROSE_FIELDS = ("responsibilities", "achievements", "description")


def _history_strings(value: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(value, str):
        if value.strip():
            out.add(value.strip())
    elif isinstance(value, dict):
        for v in value.values():
            out |= _history_strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            out |= _history_strings(v)
    return out


def non_import_prose(profile_json: dict[str, Any] | None) -> set[str]:
    """Prose texts some NON-import change wrote: the strings a change's
    ``new_value`` carries that its ``old_value`` did not, for every
    ``metadata.enrichment_history`` record whose ``source`` is not an import —
    an interview answer rendered into a bullet, a sentence typed into the CV
    section editor (#480's vector: ``manual_edit`` receipts the whole entry
    before and after, so the difference is exactly the typed text), an agent
    write. A fact about the trail (ADR-062 cl. 1), read the way
    :mod:`span_provenance` reads it."""
    if not isinstance(profile_json, dict):
        return set()
    metadata = profile_json.get("metadata")
    history = metadata.get("enrichment_history") if isinstance(metadata, dict) else None
    out: set[str] = set()
    for record in history or []:
        if not isinstance(record, dict) or record.get("source") in _IMPORT_SOURCES:
            continue
        for change in record.get("changes") or []:
            if not isinstance(change, dict) or change.get("section") == "metadata":
                continue
            out |= _history_strings(change.get("new_value")) - _history_strings(
                change.get("old_value")
            )
    return out


def imported_prose_units(profile_json: dict[str, Any] | None) -> list[str]:
    """The prose units the candidate's own imported document supplied
    (ADR-059 amended 2026-10-22, E-11 — founder ruling G-2).

    A bullet of a role, project or volunteer activity is an import unless a
    non-import change wrote it (:func:`non_import_prose`). A unit no recorded
    change names is the initial import's — the import record carries no
    per-bullet values — which is the same trust :func:`denial_release_corpus`
    already gives ``role``/``company``/``technologies[]``. Measured on the E-11
    corpus (106 containment-only decisions, two blind label sets): releasing on
    these units cuts harmful wrong floors 6 -> 2 / 7 -> 3 with zero wrong
    releases; adding interview prose released one real denial ("Settlement",
    grounded only by an interview-written bullet)."""
    if not isinstance(profile_json, dict):
        return []
    written_elsewhere = non_import_prose(profile_json)
    units: list[str] = []
    for section in _PROSE_SECTIONS:
        for entry in profile_json.get(section) or []:
            if not isinstance(entry, dict):
                continue
            for field_name in _PROSE_FIELDS:
                value = entry.get(field_name)
                for text in value if isinstance(value, list) else [value]:
                    if isinstance(text, str) and text.strip() and text.strip() not in written_elsewhere:
                        units.append(text.strip())
    return units


#: FS-7 Health item (ADR-059 amended 2026-10-24) — the parked question's
#: ``source`` and ``context.kind``: one identity for the parking and the resolution.
DENIAL_SCOPE_SOURCE = "denial_scope"
#: The answer that keeps the denial (no vault write: the floor already holds).
DENIAL_HOLDS = "denial_holds"
#: The answer that would reverse it — the ADR-059 2026-07-26 un-denial act.
#: HELD (founder, G-5, 2026-10-24): defined so the resolution branch and the
#: record share one key, but NOT offered until the ruling lands.
CV_HOLDS = "cv_holds"
DENIAL_SCOPE_OPTION_KEYS: tuple[str, ...] = (DENIAL_HOLDS,)


def denial_scope_confirmation(hit: dict[str, str]) -> PendingConfirmation:
    """THE builder of the FS-7 Health item, from one
    :func:`head_noun_scope_conflicts` record. Called by the denial recording
    path (:func:`park_denial_scope_confirmations`) and by the CV-import intake,
    so both park the same question. Stored language-independent (ADR-063 #669):
    ``question_i18n``/``options_i18n`` render per reader; ``option_keys`` is what
    the answer resolves on. Pure."""
    denial = hit["denial_concept"]
    label = hit["cv_label"]
    question_de = (
        f"Laut deiner Angabe trifft „{denial}“ auf dich nicht zu. In deinem Lebenslauf "
        f"steht aber: „{label}“. Wir zählen es deshalb weiter als nicht vorhanden. "
        "Stimmt das so?"
    )
    question_en = (
        f"By your own answer, “{denial}” does not apply to you. Your CV says: "
        f"“{label}”. So we keep counting it as not there. Is that right?"
    )
    options = {
        DENIAL_HOLDS: {
            "de": "Ja, das stimmt so",
            "en": "Yes, that's right",
        },
    }
    keys = list(DENIAL_SCOPE_OPTION_KEYS)
    return PendingConfirmation(
        question=question_en,
        options=[options[k]["en"] for k in keys],
        context={
            "kind": DENIAL_SCOPE_SOURCE,
            "denial_concept": denial,
            "bare_term": hit["bare_term"],
            "cv_label": label,
        },
        source=DENIAL_SCOPE_SOURCE,
        question_i18n={"de": question_de, "en": question_en},
        options_i18n=[dict(options[k]) for k in keys],
        option_keys=keys,
    )


def park_denial_scope_confirmations(
    metadata: ProfileMetadata | None, profile_json: dict[str, Any] | None
) -> list[PendingConfirmation]:
    """Park one FS-7 Health item per head-noun denial the candidate's own CV
    contradicts (:func:`head_noun_scope_conflicts`), never twice: a denial that
    already has a ``denial_scope`` confirmation — open OR answered — is skipped,
    so an answered question is not asked again. Returns the newly parked
    records (also appended to ``metadata.pending_confirmations``). No vault
    write besides the parked question; the floor is untouched."""
    if metadata is None:
        return []
    asked = {
        _norm(str((c.context or {}).get("denial_concept") or ""))
        for c in metadata.pending_confirmations
        if c.source == DENIAL_SCOPE_SOURCE
    }
    parked: list[PendingConfirmation] = []
    for hit in head_noun_scope_conflicts(profile_json):
        key = _norm(hit["denial_concept"])
        if key in asked:
            continue
        asked.add(key)
        conf = denial_scope_confirmation(hit)
        metadata.pending_confirmations.append(conf)
        parked.append(conf)
    return parked


def demote_ops_for_denials(
    profile: MasterProfileData, denials: list[str]
) -> list[DemoteSkill]:
    """#485 — the demote op's EMISSION RULE: which persisted vault skills this
    turn's retraction takes back (ADR-063 clause 8(e) amended 2026-08-08).

    Demotion is an **assert-class** act — it writes a negative statement about
    the candidate into their vault — so it fires on the DECLARED denial only
    (:func:`declared_denial_matches`, longest match first), never on the
    compound-containment branch: a retraction of "Tailwind CSS" is no statement
    whatsoever about the vault's bare "CSS", and demoting it would fabricate
    testimony (ADR-059 amended 2026-08-08, the assert/refuse split of #486).
    The refuse half keeps containment; only this half narrows.

    Scoped to ``confirmed`` skills, per #485's decided scope: an ``unconfirmed``
    entry is the reconciler's own inference and already backs nothing (ADR-061
    clause 3), and a ``denied`` one is already there. Certifications and
    languages are OUT of scope — the reconciler's ``denials`` array is a flat
    list of concept strings carrying no entity kind, so extending demotion to
    them would be inventing semantics the emission path cannot declare.

    Deterministic and pure: a fact read off the vault plus the model's own
    atomic declarations, never a judgement (ADR-062 clause 1), and the profile
    is never mutated — the write happens in ``apply_ops`` like every other op
    (ADR-066).
    """
    ops: list[DemoteSkill] = []
    for skill in profile.skills:
        if skill.status != "confirmed":
            continue
        matches = declared_denial_matches(skill.name, denials)
        if not matches:
            continue
        logger.info(
            "reconcile stance: demoting vault skill %r to denied — the "
            "candidate declared %r (#485, ADR-061 amended 2026-08-08)",
            skill.name, matches[0],
        )
        ops.append(DemoteSkill(name=skill.name, declared_denial=matches[0]))
    return ops
