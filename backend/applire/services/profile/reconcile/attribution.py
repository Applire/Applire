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

"""Deterministic employer-attribution guard for the ADR-046 reconciler (#243).

Ground truth (live-reproduced 2026-07-24, founder charter re-run, main @
53ffa85 — see ``tests/unit/test_reconcile_attribution.py``): a single
multi-employer interview answer named BOTH NordPharm and Applire in one turn.
The reconciler LLM correctly split the answer into several ``add_bullets``
ops, but two of them carried an Applire-only clause's text while TARGETING a
NordPharm entity (a WorkEntry and a nested ProjectEntry). This is a
model-emitted wrong-target op (prompt-side): the deterministic applier
(``apply.py``) faithfully applies whatever ``target`` the op names — by
design (ADR-046: "the applier never re-decides whether two entities are the
same"). This module is the belt-and-braces deterministic backstop the
applier layer was missing.

The pattern mirrors ``services.oracle.extract``'s letter-path employer
anchoring (``_find_employer_anchor`` / ``_employer_anchor_candidates``,
#237/#196) — kept as an independent copy here rather than a cross-package
import: ``oracle`` depends on the profile/reconcile write path's OUTPUT
(rendered documents), not the other way around, and ``apply.py``'s own
docstring commits to staying a pure, self-contained module. Importing
``oracle`` here would invert that dependency for a few dozen lines of
regex.

Design (belt AND braces, per #243):

* Scope: free-text ``add_bullets`` content (``responsibilities`` /
  ``achievements`` — NOT ``technologies``, see ``_GUARDED_FIELDS``) merging
  into an EXISTING work/project entity. New-entity creation and bulk
  ``cv_upload``/``manual`` sources are out of scope (see ``_grounding_corpus``
  reuse from ``stance.py`` — the same interview-turn-only restriction #127
  already established).
* Anchoring is at COMPANY-NAME granularity, not per-entity-id: a candidate
  who held several roles at the same employer (the live profile has three
  NordPharm stints) must not make "NordPharm" read as an ambiguous anchor
  merely because it maps to multiple entity ids — every WorkEntry sharing a
  company name collapses to ONE candidate (see ``_company_candidates``).
* A bullet's own text rarely repeats the employer name (the live bug's
  bullets name NEITHER company) — the anchor comes from the ANSWER SENTENCE
  the bullet was drawn from, located via token-overlap coverage (reusing
  ``ats_audit.skill_tokens``, the shared tokenizer — never a second one),
  not a literal substring match (the live bullets are lightly paraphrased
  from the source sentence, not verbatim quotes).
* A bullet whose owning sentence cannot be found (paraphrased beyond
  recognition), or whose owning sentence names NO employer, or the SAME
  employer as the op's target, keeps today's behaviour unchanged — over-drop
  discipline: legitimate enrichment answers rarely name any employer at all
  and must not be blocked.
* A sentence naming TWO OR MORE distinct employers is ambiguous and fails
  open (documented #243 design choice) rather than guessing.
* A genuine mismatch does NOT silently apply and does NOT silently drop the
  content — it is rerouted into a ``request_confirmation`` op (the existing
  pending-confirmation channel every other reconcile guard already uses).

Second channel — the bullet's OWN words (founder ruling M-2c, 2026-09-09)
------------------------------------------------------------------------

The sentence-anchor channel above has two documented ways of not firing, and
the 11-model matrix ran straight through both of them (7 of 11 models put a
fact on the wrong employer on the one-station shape S7 —
``o3/failure-taxonomy-2026-09-09.md`` §3.3):

1. ``_company_candidates`` reads employers off the VAULT only. On a vault
   holding one station, the two employers the answer also names simply are not
   employers as far as this module is concerned, so a bullet folding all three
   onto the one that exists anchors to its own target and passes.
2. The sentence anchor FAILS OPEN when the owning sentence names two or more
   employers (#243's deliberate choice — "ambiguous, do not guess"). The S7
   answer names three in one sentence, which is precisely the shape that
   produces the fold.

So the second channel asks a different, narrower question that cannot be
ambiguous: **do this bullet's OWN words name an employer other than the entity
it targets?** A bullet whose text says "monoclonal antibodies at Helvetia
Pharma, blood bags at the Blutspendedienst, and now mRNA vaccines at NovaRNA"
while targeting NovaRNA is mis-attributed no matter how many employers it
names — the count is not evidence of ambiguity here, it is the defect.

Two deliberate widenings, both facts and not judgements (ADR-062 clause 1):

* the candidate set is the vault's employers **plus every employer this same
  batch creates** (``upsert_work.company`` / ``upsert_volunteer.organization``).
  That is what makes the M-2 prompt rule and this witness reinforce rather than
  overlap: the rule asks the model to split the sentence into one op per named
  employer, and the moment it does, those employers become nameable here.
* ``set_field`` on a free-text employer-context field is in scope too
  (``ministral-8b`` produced the same fold through ``industry_context`` rather
  than through a bullet).

ADR-066 — ONE implementation. Both channels live in this function and share the
candidate set, the normalisation and the single ``request_confirmation``
reroute; they differ only in which text they read (the owning SENTENCE vs the
bullet's OWN words). A separate module would be a second employer-attribution
implementation, which is the thing that rule forbids.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from applire.schemas.profile import MasterProfileData, ProjectEntry, WorkEntry
from applire.services.ats_audit import skill_tokens
from applire.services.profile.reconcile.confirmations import (
    attribution_confirmation,
    attribution_entry_confirmation,
)
from applire.services.profile.reconcile.ops import (
    AddBullets,
    ReconcileOp,
    RequestConfirmation,
    SetField,
    UpsertVolunteer,
    UpsertWork,
)
from applire.services.profile.reconcile.stance import _grounding_corpus

# ── punctuation / sentence-splitting (independent copy — see module docstring) ──

_APOSTROPHE_CHARS = "’ʼ‘‛´`"
_DASH_CHARS = "‒–—―−"


def _normalize_punct(text: str) -> str:
    out = text
    for ch in _APOSTROPHE_CHARS:
        out = out.replace(ch, "'")
    for ch in _DASH_CHARS:
        out = out.replace(ch, "-")
    return out


# Academic degree abbreviations added #416 (same gap as the oracle package's
# independent copy in services.oracle.extract — see that module's
# _ABBREVIATIONS comment for the charter-run-13 ground truth). This copy
# deliberately does NOT carry oracle's "Mio."/"Mrd."/"Tsd."/"Mr."/"Mrs."/"Ms."
# additions from #398 — the two lists are allowed to diverge; only the
# degree family + the ordering fix below are shared.
#
# Ordering constraint: protection is applied by SEQUENTIAL literal
# ``str.replace`` (below), so a shorter member that is a prefix/substring of
# a longer one must not run first, or it partially sentinel-fies the longer
# match and destroys it — e.g. "Dr." vs. "Dr. rer. pol." / "Dr.-Ing.", or
# "B.A." vs. "M.B.A.". The tuple is sorted longest-first once at import time
# so the human-readable grouping above can stay unordered.
_ABBREVIATIONS = tuple(
    sorted(
        (
            "z.B.", "z. B.", "d.h.", "d. h.", "u.a.", "u. a.", "bzw.", "ggf.",
            "inkl.", "ca.", "vs.", "e.g.", "i.e.", "etc.", "approx.",
            "Dr.", "Prof.", "Nr.", "No.",
            "M.Sc.", "B.Sc.", "M.A.", "B.A.", "M.Eng.", "B.Eng.",
            "Dipl.-Ing.", "Dipl.-Kfm.", "Dipl.-Betriebsw.",
            "Dr. rer. nat.", "Dr. rer. pol.", "Dr.-Ing.",
            "LL.M.", "M.B.A.", "Ph.D.",
        ),
        key=len,
        reverse=True,
    )
)
_SENTINEL = "\x00"
_DECIMAL_DOT_RE = re.compile(r"(?<=\d)\.(?=\d)")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _split_sentences(text: str) -> list[str]:
    """Deterministic sentence split with abbreviation and decimal guards.

    Independent copy of ``services.oracle.extract.split_sentences`` (see
    module docstring for why this isn't a cross-package import).
    """
    t = (text or "").strip()
    if not t:
        return []
    protected = t
    for abbrev in _ABBREVIATIONS:
        protected = protected.replace(abbrev, abbrev.replace(".", _SENTINEL))
    protected = _DECIMAL_DOT_RE.sub(_SENTINEL, protected)
    sentences = []
    for part in _SENTENCE_SPLIT_RE.split(protected):
        restored = part.replace(_SENTINEL, ".").strip()
        if restored:
            sentences.append(restored)
    return sentences


# ── company-name anchoring ───────────────────────────────────────────────────

# Common DE/EN legal-form suffixes, stripped so "NordPharm SE" and a spoken
# "NordPharm" (or "bei NordPharm") anchor to the SAME candidate (#243 test:
# legal-form variants).
_LEGAL_FORM_RE = re.compile(
    r"\s+(?:SE|AG|GmbH(?:\s*&\s*Co\.?\s*KG)?|gGmbH|mbH|KG|OHG|GbR|"
    r"e\.\s?V\.?|Inc\.?|LLC|Ltd\.?|Co\.?|Corp\.?|Corporation|PLC|LLP)\.?\s*$",
    re.IGNORECASE,
)


def _core_company_name(name: str) -> str:
    """Legal-form-stripped company name for anchor matching."""
    stripped = _LEGAL_FORM_RE.sub("", name.strip())
    return stripped.strip() or name.strip()


def _company_candidates(profile: MasterProfileData) -> dict[str, str]:
    """core name -> display name, deduped so multiple roles at the SAME
    employer (the live profile has three NordPharm stints) collapse to ONE
    anchor candidate rather than reading as an ambiguous multi-entity match.
    """
    candidates: dict[str, str] = {}
    for w in profile.work_experience:
        company = (w.company or "").strip()
        if not company:
            continue
        core = _core_company_name(company)
        if core and core not in candidates:
            candidates[core] = company
    return candidates


def _anchor_company(text: str, candidates: dict[str, str]) -> tuple[str, str] | None:
    """The (core, display) company name ``text`` names, iff EXACTLY ONE
    (fail open on zero or ambiguous — #243 documented design choice)."""
    if not text or not candidates:
        return None
    normalized = _normalize_punct(text)
    found: set[str] = set()
    for core in candidates:
        pattern = re.compile(r"\b" + re.escape(core) + r"\b", re.IGNORECASE)
        if pattern.search(normalized):
            found.add(core)
    if len(found) == 1:
        core = next(iter(found))
        return core, candidates[core]
    return None


# ── owning-sentence lookup (token-overlap coverage, not literal substring) ──

_COVERAGE_MIN = 0.7


def _owning_sentence(bullet: str, sentences: list[str]) -> str | None:
    """The sentence ``bullet`` was most likely drawn from, or ``None``.

    The reconciler paraphrases lightly ("I built a deterministic verification
    layer, the Truthfulness Oracle, that audits" -> "Built deterministic
    verification layer (Truthfulness Oracle) auditing") — a literal substring
    check misses this, so we measure how much of the bullet's own content-token
    set is covered by each sentence (reusing ``ats_audit.skill_tokens``, the
    shared tokenizer) and take the best match. Below ``_COVERAGE_MIN`` the
    bullet is treated as unlocatable — fail open (see module docstring).
    """
    bullet_tokens = skill_tokens(bullet)
    if not bullet_tokens:
        return None
    best_sentence: str | None = None
    best_coverage = 0.0
    for sentence in sentences:
        sentence_tokens = skill_tokens(sentence)
        if not sentence_tokens:
            continue
        hits = len(bullet_tokens & sentence_tokens)
        coverage = hits / len(bullet_tokens)
        if coverage > best_coverage:
            best_coverage = coverage
            best_sentence = sentence
    if best_coverage >= _COVERAGE_MIN:
        return best_sentence
    return None


# ── entity <-> employer resolution ───────────────────────────────────────────


def _resolve_existing(target: str | None, profile: MasterProfileData) -> Any | None:
    """An EXISTING work/project entity by id (never a same-batch local ref —
    this guard runs before ``apply_ops`` creates anything, so an unresolvable
    target is either a not-yet-created local ref or unknown; either way,
    fail open)."""
    if not target:
        return None
    for entry in (*profile.work_experience, *profile.projects):
        if getattr(entry, "id", None) == target:
            return entry
    return None


def _entity_employer_core(entity: Any, profile: MasterProfileData) -> str | None:
    """The core company name ``entity`` belongs to, or ``None`` when there is
    no employer context to guard against (a standalone project)."""
    if isinstance(entity, WorkEntry):
        return _core_company_name(entity.company) if entity.company else None
    if isinstance(entity, ProjectEntry):
        assoc = entity.associated_experience
        if assoc:
            parent = next(
                (w for w in profile.work_experience if w.id == assoc), None
            )
            if parent is not None and parent.company:
                return _core_company_name(parent.company)
        return None
    return None


# ── the bullet's own words (M-2c) ────────────────────────────────────────────

# `set_field` fields whose VALUE is free text about the entity's employer
# context. `industry_context` is the one `ministral-8b` folded a three-employer
# span into on S7 (taxonomy §3.3). Deliberately a closed list: `set_field` also
# fills dates, ids and enum-ish scalars, and a company name inside one of those
# means something else entirely.
_GUARDED_SET_FIELDS = frozenset({"industry_context", "description", "summary"})


def _batch_company_candidates(ops: list[ReconcileOp]) -> dict[str, str]:
    """core -> display for every employer THIS BATCH creates.

    The vault cannot name an employer it does not hold, which is exactly the
    hole the one-station shape falls through. An `upsert_work` in the same
    batch is the model itself declaring "this is an employer" — a fact on the
    record, not an inference — so it joins the candidate set for this turn.
    """
    found: dict[str, str] = {}
    for op in ops:
        name = ""
        if isinstance(op, UpsertWork):
            name = (op.company or "").strip()
        elif isinstance(op, UpsertVolunteer):
            name = (op.organization or "").strip()
        if not name:
            continue
        core = _core_company_name(name)
        if core and core not in found:
            found[core] = name
    return found


def _employers_named_in(text: str, candidates: dict[str, str]) -> set[str]:
    """Every candidate core whose name appears in ``text``. A FACT: literal,
    word-bounded, legal-form-insensitive presence — never a judgement about
    whether the mention "means" the bullet belongs there."""
    if not text or not candidates:
        return set()
    normalized = _normalize_punct(text)
    return {
        core
        for core in candidates
        if re.search(r"\b" + re.escape(core) + r"\b", normalized, re.IGNORECASE)
    }


# A bullet naming another company as a business RELATIONSHIP — a client, an
# account, an acquirer/acquiree, a parent or a supplier — is not naming a
# place it worked. "Key account manager for the Siemens account" while
# employed at Bosch names Siemens as the candidate's CLIENT, not their
# employer; "migrated the platform after the acquisition by NordPharm" names
# the buyer, not a second job. Channel 2 (below) has no ambiguity fail-open
# of its own — unlike the owning-sentence channel (#243), which already lets
# a sentence naming two-or-more employers pass unguessed — so without this
# exclusion it re-flags exactly the shape #243's own test suite documents as
# the correct, deliberate non-guess (`test_ambiguous_two_employers_in_one_
# clause_fails_open`) the moment the same two names land in one BULLET
# instead of one sentence (adversarial pass 2026-09-10, found and fixed on
# this branch: `test_a_bullet_naming_a_client_or_acquirer_is_not_rerouted`).
#
# A fixed, closed vocabulary — the same "narrow, closed, documented list"
# idiom `_LEGAL_FORM_RE` / `_ABBREVIATIONS` already use above — never an
# open-ended judgement about what a mention "means" (ADR-062 clause 1): a
# bullet containing one of these words is read as describing a RELATIONSHIP
# to the other company, a fact about the bullet's own text, not a claim about
# whether the mention is innocent in any particular case.
_RELATIONAL_MARKER_RE = re.compile(
    r"\b(?:account|client|customer|vendor|supplier|partner|on behalf of|"
    r"acquisition|acquisitions|acquired|acquires?|acquiring|merger|mergers|"
    r"merged|subsidiary|subsidiaries|parent company|brand of|"
    r"kunde|kunden|kundenbetreuung|lieferant|übernahme|übernommen|"
    r"tochtergesellschaft|muttergesellschaft|fusion|im auftrag von)\b",
    re.IGNORECASE,
)


def _foreign_employers(text: str, candidates: dict[str, str], target_core: str) -> set[str]:
    """The employers ``text`` names that are NOT the op's target.

    Suppressed entirely when ``text`` also carries a relational marker (see
    ``_RELATIONAL_MARKER_RE``) — a bullet mentioning a client/acquirer/parent
    by name is not thereby claiming to have worked there.
    """
    if _RELATIONAL_MARKER_RE.search(text or ""):
        return set()
    return {core for core in _employers_named_in(text, candidates) if core != target_core}


# ── clause scoping: the model's OWN split is the evidence (#674 L34, #723) ───


def _guarded_content_ops(
    ops: list[ReconcileOp], profile: MasterProfileData
) -> list[tuple[ReconcileOp, str]]:
    """Every op in the batch that puts guarded content on a KNOWN employer, with
    that employer's core name.

    The shared first pass of both clause-scoping rules below. It reads exactly
    what the two channels read — ``add_bullets`` free text and a guarded
    ``set_field`` value — so "the employers this batch writes to" can never
    drift from "the employers the guard adjudicates".
    """
    out: list[tuple[ReconcileOp, str]] = []
    for op in ops:
        if isinstance(op, AddBullets):
            if not any(getattr(op, f) for f in _BULLET_FIELDS):
                continue
        elif isinstance(op, SetField):
            if op.field not in _GUARDED_SET_FIELDS or not isinstance(op.value, str):
                continue
        else:
            continue
        entity = _resolve_existing(op.target, profile)
        core = _entity_employer_core(entity, profile) if entity is not None else None
        if core:
            out.append((op, core))
    return out


def _muted_sentences(
    content_ops: list[tuple[ReconcileOp, str]], sentences: list[str]
) -> set[str]:
    """Sentences the MODEL has already split across employers (#674 line 34).

    #243's design says a sentence naming two or more distinct employers is
    ambiguous and must fail open rather than guess. Its detector for that —
    ``_anchor_company``'s literal, legal-form-stripped CORE-name match — badly
    under-counts: measured over all 80 captured #684 spike records, the compact
    three-station sentence reads as naming exactly ONE employer, because
    "NovaRNA" is not the core "NovaRNA Biotech" and "the blood donation service"
    is not "Blutspendedienst Nord". So the ambiguity rule never engaged and
    **23 of 29 flagged items were over-fires**: correctly targeted bullets
    ("blood bags at the blood donation service" -> Blutspendedienst) each asked
    about against Helvetia Pharma, 10/10 runs on that shape.

    The fix keys the same rule on evidence the model itself produced instead:
    a sentence anchors nothing when the batch **PARTITIONS** it — draws guarded
    content from it for two or more distinct target employers, and gives no two
    of those employers the same text. A partition is the model attributing that
    sentence at a granularity this guard cannot read, so the guard must not
    re-attribute one share of it to the single employer it could literally
    match. A FACT about the op batch (ADR-062 clause 1) — never a clause
    splitter, and channel 2 (the bullet's own words, M-2c) is untouched.

    **Why "partition" and not merely "two employers".** #243's own ground truth
    (`test_reconcile_attribution.py`, live turn 2026-07-24) is a batch that gave
    the SAME bullet — "Built deterministic verification layer (Truthfulness
    Oracle) …" — to both the NordPharm role and the Applire role. Two employers
    drew from the sentence, but that is not a split; it is the model
    contradicting itself, and the anchor is exactly what catches it. A shared
    text anywhere among the drawing employers therefore leaves the sentence
    speaking.

    Measured effect over the 80 captured records: channel-1 flags 23 -> 2,
    channel 2 unchanged at 6, and #243's live incident still routes both
    mis-targeted ops into the confirmation.
    """
    per_sentence: dict[str, dict[str, set[str]]] = {}
    for op, core in content_ops:
        texts = (
            [t for f in _BULLET_FIELDS for t in getattr(op, f)]
            if isinstance(op, AddBullets)
            else [op.value]
        )
        for text in texts:
            sentence = _owning_sentence(text, sentences)
            if sentence is not None:
                per_sentence.setdefault(sentence, {}).setdefault(core, set()).add(text)
    muted: set[str] = set()
    for sentence, by_core in per_sentence.items():
        if len(by_core) < 2:
            continue
        shares = list(by_core.values())
        if any(
            shares[i] & shares[j]
            for i in range(len(shares))
            for j in range(i + 1, len(shares))
        ):
            continue  # a duplicated text is not a partition — see above
        muted.add(sentence)
    return muted


def _batch_anchor(
    content_ops: list[tuple[ReconcileOp, str]],
    corpus: str,
    sentences: list[str],
    candidates: dict[str, str],
) -> tuple[str, str] | None:
    """The employer the WHOLE answer is about, when the batch contradicts it.

    #723's unflagged sibling: the answer was "Als ich 2018 zu BioNTech kam …
    Teamleitung während der Elternzeit", the reconciler put every bullet of it
    under the PREVIOUS employer, and the guard flagged only the one bullet whose
    own text repeats "BioNTech". The sibling and the ``technologies`` from the
    same answer merged silently — the bullet's own words say nothing (channel 2
    is blind), and the owning-sentence channel cannot reach across the answer's
    German and the bullet's English (token coverage ~0).

    The narrow fact that survives both blindnesses: **this batch writes guarded
    content to exactly ONE employer, and the answer names exactly one, and they
    are different.** There is then no split to respect and no clause to guess —
    the whole op is held, and the candidate's answer places all of it (ADR-063
    am. 2026-09-18 clause 1: ``keep_here`` puts it back, so nothing is lost by
    asking). ``None`` whenever the batch touches two or more employers — the
    model split it, and rule (a) above governs instead.

    Measured over the same 80 captured records: fires on 10 (all S7 — the
    one-station vault, the very fold shape founder ruling M-2c was written for),
    holding 12 bullets that ship silently today, and on 0 of the four
    multi-station shapes.
    """
    cores = {core for _, core in content_ops}
    if len(cores) != 1:
        return None
    anchor = _anchor_company(corpus, candidates)
    if anchor is None or anchor[0] in cores:
        return None
    # The same exclusion channel 2 makes per bullet (`_foreign_employers`),
    # applied to the sentences that actually NAME the anchor rather than to the
    # whole answer: an answer about the Siemens ACCOUNT while employed at Bosch
    # names Siemens as a client, not a second job
    # (`test_set_field_naming_a_client_account_is_not_rerouted`). Scoped to the
    # naming sentences on purpose — "übernommen" ("took over") is in the marker
    # vocabulary for "Übernahme"/acquisition, and #723's own answer says
    # "Teamleitung … übernommen" in a LATER sentence, which at corpus
    # granularity silenced the channel on the very case it exists for.
    naming = [
        sentence
        for sentence in sentences
        if anchor[0] in _employers_named_in(sentence, {anchor[0]: anchor[1]})
    ]
    if naming and all(_RELATIONAL_MARKER_RE.search(s) for s in naming):
        return None
    return anchor


# ── the guard itself ─────────────────────────────────────────────────────────

# Technologies are short generic nouns ("Databricks", "LangGraph") — the
# owning-sentence coverage check is unreliable at that granularity (a
# one-token bullet trivially "covers" many unrelated sentences), so they stay
# out of scope OF CHANNELS 1 AND 2; the live incident's misattributions were
# both free-text achievement/responsibility bullets.
_GUARDED_FIELDS = ("responsibilities", "achievements")

#: Channel 3 (``_batch_anchor``) reads no bullet text at all — it decides from
#: the batch's own targeting — so the granularity objection above does not apply
#: and ``technologies`` is in scope there. #723: the same answer's
#: ``technologies: ["Clean Code", "Software architecture"]`` merged under the
#: wrong employer beside the sibling bullet, for exactly this reason.
_BULLET_FIELDS = ("responsibilities", "achievements", "technologies")


def _build_confirmation(
    op: AddBullets,
    entity: Any,
    flagged: list[tuple[str, str, str]],
    target_display: str,
) -> RequestConfirmation:
    anchor_displays = sorted({display for _, _, display in flagged})
    anchor_text = " / ".join(anchor_displays)
    sample = flagged[0][1]
    section = "work_experience" if isinstance(entity, WorkEntry) else "projects"
    return attribution_confirmation(
        sample=sample,
        anchor_text=anchor_text,
        target_display=target_display,
        context={
            "section": section,
            "target": op.target,
            "target_employer": target_display,
            "anchor_employer": anchor_text,
            "flagged": [{"field": field, "text": text} for field, text, _ in flagged],
        },
    )


def enforce_attribution(
    ops: list[ReconcileOp],
    *,
    profile: MasterProfileData,
    new_info: Any,
    source: str,
) -> list[ReconcileOp]:
    """Deterministic belt on top of the reconciler's own entity-matching (#243).

    For every ``add_bullets`` op targeting an EXISTING work/project entity,
    each incoming responsibility/achievement bullet is checked against the
    ONE employer its owning answer-sentence names (if any). A bullet that
    anchors to a DIFFERENT employer than its target entity's own employer is
    pulled out of the op and rerouted into a ``request_confirmation`` — never
    silently applied, never silently dropped. Everything else (no anchor,
    same-employer anchor, ambiguous anchor, non-interview sources) is
    returned unchanged — over-drop discipline: legitimate enrichment must
    keep working.
    """
    corpus = _grounding_corpus(new_info, source)
    if corpus is None:
        return ops
    corpus = _normalize_punct(corpus)
    sentences = _split_sentences(corpus)
    if not sentences:
        return ops
    # M-2c: the vault's employers PLUS the ones this batch is creating. On a
    # one-station vault the second half is the only thing that can name the
    # employers the answer just introduced.
    candidates = {**_batch_company_candidates(ops), **_company_candidates(profile)}
    if not candidates:
        return ops

    # Clause scoping (ADR-063 amended 2026-09-18): one pass over the batch,
    # read by both new rules — the sentences the model itself already split
    # (rule a, #674 line 34) and the one-employer batch a one-employer answer
    # contradicts (rule b, #723's unflagged sibling).
    content_ops = _guarded_content_ops(ops, profile)
    muted = _muted_sentences(content_ops, sentences)
    batch_anchor = _batch_anchor(content_ops, corpus, sentences, candidates)

    result: list[ReconcileOp] = []
    for op in ops:
        if isinstance(op, SetField):
            result.extend(_guard_set_field(op, profile, candidates, batch_anchor))
            continue
        if not isinstance(op, AddBullets):
            result.append(op)
            continue

        entity = _resolve_existing(op.target, profile)
        target_core = _entity_employer_core(entity, profile) if entity is not None else None
        if target_core is None:
            result.append(op)
            continue
        target_display = candidates.get(target_core, target_core)

        kept: dict[str, list[str]] = {}
        flagged: list[tuple[str, str, str]] = []
        for field in _BULLET_FIELDS:
            kept_bullets: list[str] = []
            for bullet in getattr(op, field):
                if field in _GUARDED_FIELDS:
                    # Channel 2 (M-2c) FIRST: the bullet's own words are decisive
                    # and cannot be ambiguous — a bullet naming an employer other
                    # than its target is mis-attributed however many it names.
                    foreign = _foreign_employers(bullet, candidates, target_core)
                    if foreign:
                        flagged.append(
                            (field, bullet, " / ".join(sorted(candidates[c] for c in foreign)))
                        )
                        continue
                    # Channel 1 (#243): the OWNING SENTENCE, for the common case
                    # of a bullet that names no employer at all in its own text —
                    # silent on a sentence the model already split (rule a).
                    sentence = _owning_sentence(bullet, sentences)
                    if sentence is not None and sentence not in muted:
                        anchor = _anchor_company(sentence, candidates)
                        if anchor is not None and anchor[0] != target_core:
                            flagged.append((field, bullet, anchor[1]))
                            continue
                # Channel 3 (rule b): neither channel above can see across a
                # translated paraphrase, and this batch names one employer while
                # the answer names another. Held, not re-attributed — unless the
                # bullet's own words describe a RELATIONSHIP, the exclusion
                # channel 2 already makes.
                if (
                    batch_anchor is not None
                    and batch_anchor[0] != target_core
                    and not _RELATIONAL_MARKER_RE.search(bullet)
                ):
                    flagged.append((field, bullet, batch_anchor[1]))
                    continue
                kept_bullets.append(bullet)
            kept[field] = kept_bullets

        if not flagged:
            result.append(op)
            continue

        if any(kept.values()):
            result.append(op.model_copy(update=kept))
        result.append(_build_confirmation(op, entity, flagged, target_display))

    return result


def _guard_set_field(
    op: SetField,
    profile: MasterProfileData,
    candidates: dict[str, str],
    batch_anchor: tuple[str, str] | None = None,
) -> list[ReconcileOp]:
    """M-2c for `set_field`: the same question, a whole VALUE instead of a bullet.

    `ministral-8b` folded the three-employer span into `industry_context` rather
    than into a bullet (taxonomy §3.3), so the same fact reaches the same vault
    slot through an op the #243 guard never looked at. There is no partial keep
    here — a scalar is one value — so the op is replaced by the confirmation
    outright: never silently applied, never silently dropped.
    """
    if op.field not in _GUARDED_SET_FIELDS or not isinstance(op.value, str):
        return [op]
    entity = _resolve_existing(op.target, profile)
    target_core = _entity_employer_core(entity, profile) if entity is not None else None
    if target_core is None:
        return [op]
    foreign = _foreign_employers(op.value, candidates, target_core)
    if foreign:
        anchor_text = " / ".join(sorted(candidates[c] for c in foreign))
    elif (
        batch_anchor is not None
        and batch_anchor[0] != target_core
        and not _RELATIONAL_MARKER_RE.search(op.value)
    ):
        # Channel 3 (rule b) reaches the scalar too: `ministral-8b` folded a
        # three-employer span into `industry_context` rather than into a bullet
        # (taxonomy §3.3), and a fold whose text names no employer at all is
        # exactly what the batch-level anchor is for.
        anchor_text = batch_anchor[1]
    else:
        return [op]
    target_display = candidates.get(target_core, target_core)
    section = "work_experience" if isinstance(entity, WorkEntry) else "projects"
    return [
        attribution_confirmation(
            sample=op.value,
            anchor_text=anchor_text,
            target_display=target_display,
            context={
                "section": section,
                "target": op.target,
                "target_employer": target_display,
                "anchor_employer": anchor_text,
                "flagged": [{"field": op.field, "text": op.value}],
            },
        )
    ]


# ── resolving an answered attribution ask (Bug #723) ─────────────────────────

#: The one implementation of "which entry does this attribution answer place the
#: held content on". A PLAN, not a write: `apply.py` executes it against the
#: profile it is already editing, so the resolution reaches every route through
#: the one committer rather than through one answer handler — the door-parity
#: hole the 2026-09-09 adversarial pass found in founder ruling V-5's first
#: build, not repeated here.
@dataclass(frozen=True)
class AttributionPlan:
    #: ``(entity_id, field, text)`` — appended to that entity's bullet list.
    placements: tuple[tuple[str, str, str], ...] = ()
    #: ``(entity_id, field, value)`` — written into an EMPTY scalar slot only.
    scalars: tuple[tuple[str, str, str], ...] = ()
    #: ``(section, label, reason)`` — an ``ImportNotApplied`` item the applier mints.
    not_applied: tuple[tuple[str, str, str], ...] = ()
    #: The narrower keyed ask, parked when the anchor names several roles.
    followup: RequestConfirmation | None = None


_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")

#: The held text, cut for a receipt label. Long enough that the candidate
#: recognises their own sentence, short enough that a receipt stays a receipt —
#: the same truncation idiom `compute_no_write`'s residue label uses.
_LABEL_CHARS = 120


def _held_label(field: str, text: str) -> str:
    body = " ".join((text or "").split())
    if len(body) > _LABEL_CHARS:
        body = body[: _LABEL_CHARS - 1].rstrip() + "…"
    return f"{field}: {body}"


def _anchor_entries(anchor_text: str, profile: MasterProfileData) -> list[WorkEntry]:
    """Every work entry the ANSWER's employer name(s) identify.

    `anchor_employer` is a DISPLAY string, and `_build_confirmation` joins
    several with " / " when one op was flagged against more than one employer.
    Matching is on the same legal-form-stripped core the guard anchors on, so
    "BioNTech SE" in the ask and "BioNTech" in the vault are one employer — and
    a candidate with three stints there yields three entries, which is exactly
    the cardinality founder ruling V-1 governs.
    """
    cores = {
        _core_company_name(part.strip())
        for part in (anchor_text or "").split(" / ")
        if part.strip()
    }
    cores = {c.casefold() for c in cores if c}
    if not cores:
        return []
    return [
        w
        for w in profile.work_experience
        if w.company and _core_company_name(w.company).casefold() in cores
    ]


def _covers_year(entry: WorkEntry, year: int) -> bool:
    """Whether ``year`` falls inside this role's own date range.

    Clock-free on purpose (this module is pure): an open-ended role — no end
    date, or `is_current` — covers every year from its start onwards, so a role
    the candidate still holds never needs today's date to be decided.
    """
    start = (entry.start_date or "")[:4]
    if not start.isdigit():
        return False
    if int(start) > year:
        return False
    end = (entry.end_date or "")[:4]
    if entry.is_current or not end.isdigit():
        return True
    return year <= int(end)


def _entry_label(entry: WorkEntry) -> str:
    span = "–".join(p for p in [(entry.start_date or ""), (entry.end_date or "")] if p)
    if entry.is_current and entry.start_date and not entry.end_date:
        span = f"{entry.start_date}–"
    role = (entry.role or "").strip() or (entry.company or "").strip()
    return f"{role} ({span})" if span else role


def _place_all(
    entity_id: str, flagged: list[tuple[str, str]]
) -> tuple[tuple[tuple[str, str, str], ...], tuple[tuple[str, str, str], ...]]:
    placements = tuple(
        (entity_id, field, text) for field, text in flagged if field in _BULLET_FIELDS
    )
    scalars = tuple(
        (entity_id, field, text)
        for field, text in flagged
        if field in _GUARDED_SET_FIELDS
    )
    return placements, scalars


def plan_attribution_resolution(
    context: dict[str, Any], option_key: str | None, profile: MasterProfileData
) -> AttributionPlan | None:
    """Turn an answered attribution ask into what the vault should now hold.

    Bug #723: the guard HOLDS the content (it is removed from the op and carried
    only in `context["flagged"]`), and the resolution used to write the metadata
    CLEAR and nothing else — so the candidate placed an attested fact and the
    fact was gone, under a receipt reading "Recorded your answer". Twice in one
    edge run, `triage:vault-integrity`.

    ``None`` means "not this family" — a model-emitted `request_confirmation`
    (prompt rule 6) carries neither `option_keys` nor `flagged`, and a record
    persisted before #669 carries no keys either; both keep the pre-#723
    bookkeeping-only behaviour, correctly: nothing was held.

    Founder ruling V-1 / D-6 (2026-09-18) on cardinality: exactly one candidate
    entry places it; several, with one 4-digit year in the held text falling
    into exactly one role's range, places it there; anything else asks the
    narrower keyed question and keeps the content held. Never "the most recent
    role".

    **Adversarial pass, 2026-09-19.** `option_key` can be `None` for a record
    that DOES carry `flagged`/`anchor_employer` too: `resolve_option_key` only
    matches an answer against a rendered option EXACTLY, and the agent channel
    is free text, not a button click — an answer that paraphrases rather than
    echoes a rendered option (or any other unrecognised key) used to fall
    through every named branch to the same `return None` the "not this family"
    case returns, silently repeating #723 on a well-formed ask. Once `flagged`
    and `anchor_employer` are both present this function commits to being
    family 4, so every remaining path returns a plan — an unrecognised
    `option_key` parks the content exactly like the ambiguous-candidates case
    below (`not_applied=held`, still `confirmation_held`, answerable again),
    never a bare `None`.
    """
    flagged = [
        (str(item.get("field") or ""), str(item.get("text") or ""))
        for item in (context.get("flagged") or [])
        if isinstance(item, dict) and (item.get("text") or "").strip()
    ]
    if not flagged or not context.get("anchor_employer"):
        return None
    section = str(context.get("section") or "work_experience")
    held = tuple(
        (section, _held_label(field, text), "confirmation_held") for field, text in flagged
    )

    def _lost(reason: str) -> tuple[tuple[str, str, str], ...]:
        return tuple((section, _held_label(f, t), reason) for f, t in flagged)

    if option_key == "discard":
        return AttributionPlan(not_applied=_lost("confirmation_discarded"))

    if option_key == "keep_here":
        target = context.get("target")
        if not target or _resolve_existing(str(target), profile) is None:
            return AttributionPlan(not_applied=_lost("confirmation_unresolvable"))
        placements, scalars = _place_all(str(target), flagged)
        return AttributionPlan(placements=placements, scalars=scalars)

    # The narrower ask's own answer: an ENTRY id, never a rendered label (#669).
    if option_key and option_key.startswith("entry:"):
        entity_id = option_key.split(":", 1)[1]
        if not entity_id or _resolve_existing(entity_id, profile) is None:
            return AttributionPlan(not_applied=_lost("confirmation_unresolvable"))
        placements, scalars = _place_all(entity_id, flagged)
        return AttributionPlan(placements=placements, scalars=scalars)

    if option_key != "move":
        # Not one of the recognised keys — an answer `resolve_option_key`
        # could not match against any rendering (or a caller-supplied key
        # this family has never had). This IS family 4 (flagged content is
        # held), so the content is parked, never silently dropped.
        return AttributionPlan(not_applied=held)

    anchor_text = str(context.get("anchor_employer") or "")
    candidates = _anchor_entries(anchor_text, profile)
    if not candidates:
        return AttributionPlan(not_applied=_lost("confirmation_unresolvable"))
    if len(candidates) > 1:
        years = {int(y) for _, text in flagged for y in _YEAR_RE.findall(text)}
        covering = (
            [e for e in candidates if _covers_year(e, next(iter(years)))]
            if len(years) == 1
            else []
        )
        if len(covering) != 1:
            return AttributionPlan(
                not_applied=held,
                followup=attribution_entry_confirmation(
                    sample=flagged[0][1],
                    anchor_text=anchor_text,
                    candidates=[
                        (str(e.id), _entry_label(e)) for e in candidates if e.id
                    ],
                    context={
                        "section": section,
                        "anchor_employer": anchor_text,
                        "target_employer": anchor_text,
                        "flagged": [
                            {"field": field, "text": text} for field, text in flagged
                        ],
                    },
                ),
            )
        candidates = covering
    entity_id = str(candidates[0].id or "")
    if not entity_id:
        return AttributionPlan(not_applied=_lost("confirmation_unresolvable"))
    placements, scalars = _place_all(entity_id, flagged)
    return AttributionPlan(placements=placements, scalars=scalars)
