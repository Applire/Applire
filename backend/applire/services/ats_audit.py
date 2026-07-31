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

"""ADR-039 ATS audit engine — deterministic, local-only (pypdf + stdlib).

Never imports an LLM provider; never touches the network. The *_text seam
exists so unit tests run without Chromium; extraction correctness itself is
enforced by tests/ats/test_roundtrip.py.
"""

import re
import unicodedata
from io import BytesIO
from typing import Any, Literal

from pypdf import PdfReader

from applire.norms import DEFAULT_REGION, REGION_NORMS
from applire.schemas.ats import ATSCheck, ATSKeywordCoverage, ATSReport
from applire.schemas.cv import TailoredCVData


def extract_text_and_pages(pdf_bytes: bytes) -> tuple[str, int]:
    """Extracted text plus page count from a single PdfReader pass (#171a)."""
    reader = PdfReader(BytesIO(pdf_bytes))
    pages = reader.pages
    text = "\n".join((page.extract_text() or "") for page in pages)
    return text, len(pages)


def extract_text(pdf_bytes: bytes) -> str:
    return extract_text_and_pages(pdf_bytes)[0]


def _norm(s: str) -> str:
    s = s.replace("­", "")  # soft hyphens from PDF line-breaking
    s = unicodedata.normalize("NFKC", s)
    # US212 (#122): fold hyphens/dashes to spaces so "Code-Review" ≡ "code review".
    # Applied to needle and haystack alike, so matching stays symmetric.
    s = re.sub(r"[-‐-―−]", " ", s)
    return re.sub(r"\s+", " ", s).lower().strip()


def _find(needle: str, haystack_norm: str) -> int:
    """First index of normalised needle in pre-normalised haystack; -1 if absent or empty."""
    n = _norm(needle)
    if not n:
        return -1
    return haystack_norm.find(n)


# US212 minimum sizes for the morphological fold: strip a trailing "s" only when the
# remaining stem keeps ≥ 4 chars ("reviews" → "review", but never "SaaS" → "saa" or
# "K8s" → "k8"); append an "s" only to an alphabetic-final token of ≥ 4 chars.
_FOLD_MIN_STEM = 4


def _fold_variants(needle_norm: str) -> list[str]:
    """Deterministic singular/plural variants of a normalised phrase (final token only).

    US212 (#122, ADR-048 amended 2026-07-04): generosity lives in the matching
    layer — "Code reviews" must match a document that says "code review standards".
    Purely morphological, guarded, no LLM.
    """
    variants = [needle_norm]
    last = needle_norm.rsplit(" ", 1)[-1]
    if last.endswith("s") and len(last) - 1 >= _FOLD_MIN_STEM:
        variants.append(needle_norm[:-1])
    elif not last.endswith("s") and len(last) >= _FOLD_MIN_STEM and last[-1].isalpha():
        variants.append(needle_norm + "s")
    return variants


# Friction finding (#234-adjacent): conservative English verb-form fold, added
# to `surface_present`'s TOKEN-LEVEL FALLBACK only — it must never touch
# `_fold_variants` (the phrase-level substring fold `surface_present` tries
# first) nor `skill_tokens`/`skills_near_dupe` (the ADR-046 dedupe instruments,
# deliberately strictness-hardened). Checked longest-suffix-first so "mentoring"
# strips to "mentor" via "-ing", not a shorter/wrong suffix.
#
# "ship" (#244-adjacent, live-reproduced 2026-07-24): a CV skill claim
# "Mentoring" audited unbacked against a vault carrying only "...Mentorship"
# (a same-stem derivational NOUN form, not a plural or a verb tense) — the
# keyword ledger, built by the LLM, happily cited the connection, but neither
# `skills_near_dupe` (single differing tokens, containment needs >= 2 tokens,
# Jaccard 0) nor the pre-existing -ing/-ed/-es/-s fold could see it. Same
# guard rail (min stem length, single-token forms only, ADD-only) as the rest
# of this fallback — never touches skill_tokens/skills_near_dupe either.
_VERB_SUFFIXES = ("ship", "ing", "ed", "es", "s")


def _verb_stem(token: str) -> str:
    """Strip one trailing verb-form/derivational-noun suffix (-ship/-ing/-ed/
    -es/-s), only when the remaining stem keeps >= ``_FOLD_MIN_STEM`` chars —
    same guard rail as the plural fold, so short tokens ('AI', 'SaaS',
    'airship' -> 'air') never fold. Longest suffix checked first (order
    above) so "mentorship" -> "mentor" via "-ship", not a partial/wrong
    match. No suffix applies -> unchanged."""
    for suf in _VERB_SUFFIXES:
        if token.endswith(suf) and len(token) - len(suf) >= _FOLD_MIN_STEM:
            return token[: -len(suf)]
    return token


def _verb_form_present(form_norm: str, text_norm: str) -> bool:
    """Token-level verb-form fallback (bounded, conservative, English only):
    True only when ``form_norm`` is a SINGLE token that shares a stem — after
    stripping -ing/-ed/-es/-s, both directions, stem length >= 4 — with some
    single token in ``text_norm``. Multi-token forms are refused outright:
    reordering/POS-shuffling a phrase ("test automation" vs "automated tests")
    is paraphrase-level matching, explicitly out of scope (do not attempt it
    here — see ``surface_present`` docstring).
    """
    form_tokens = form_norm.split()
    if len(form_tokens) != 1:
        return False
    needle_stem = _verb_stem(form_tokens[0])
    return any(_verb_stem(word) == needle_stem for word in text_norm.split())


def surface_present(form: str, text_norm: str) -> bool:
    """THE presence predicate (US212): is this surface form in this normalised text?

    Single shared instrument for the ATS panel, the gap hints (#117), and the
    generation-time coverage check (US213) — consumers may never disagree on
    presence by construction (ADR-048 amended 2026-07-04, #122).

    Falls back to a conservative same-stem ENGLISH VERB-FORM fold (#234-adjacent
    friction finding, extended #244-adjacent) when the direct phrase-level
    substring/plural check misses: "Mentoring" (keyword) is present in text
    that only says "Mentored" (and vice versa) — or, via the "-ship"
    derivational-noun suffix, only says "Mentorship". Bounded to single-token
    forms — this can only ever ADD a match, never remove one, and never
    attempts multi-token paraphrase matching ("performance optimization" vs
    "improving ... performance" stays a true miss, by design).
    """
    n = _norm(form)
    if not n:
        return False
    if any(text_norm.find(v) >= 0 for v in _fold_variants(n)):
        return True
    return _verb_form_present(n, text_norm)


# ── #172: near-duplicate skill detection ─────────────────────────────────────
# ONE shared instrument for the reconciler (merge on import, apply.py), the
# render-side CV skill dedup (cv.py), and the ATS "skills-near-dupe" audit — so
# the three layers can never disagree on what counts as the same skill by another
# name (the coverage-vs-heal lesson, #122: the loop that grades is the loop that
# heals). Deterministic, no LLM.

_SKILL_STOPWORDS = frozenset(
    {"and", "or", "the", "of", "for", "with", "a", "an", "to", "in", "&"}
)
# Punctuation stripped only from token EDGES, so "(gxp," → "gxp" and "csv)" → "csv"
# while token-internal symbols survive ("C#", "CI/CD", "C++").
_SKILL_EDGE_PUNCT = "()[]{},;:.\"'`"
_NEAR_DUPE_JACCARD = 0.75


def _skill_stem(token: str) -> str:
    """Guarded singular fold, consistent with ``_fold_variants``: drop a trailing
    "s" only when the stem keeps ≥ ``_FOLD_MIN_STEM`` chars (never "SaaS" → "saa").

    Only *purely-alphabetic* tokens are folded — a token with internal punctuation
    ('node.js', 'ci/cd') is a proper noun / identifier, not an English plural, so
    stripping its trailing 's' would corrupt it ('node.js' → 'node.j')."""
    if token.isalpha() and token.endswith("s") and len(token) - 1 >= _FOLD_MIN_STEM:
        return token[:-1]
    return token


def skill_tokens(name: str) -> frozenset[str]:
    """The normalised content-token set of a skill name (#172).

    ``_norm`` (NFKC, dash→space, casefold, whitespace collapse) then edge-punctuation
    stripping, conjunction/article removal, and a guarded plural fold — so
    formatting and morphological variants ('Code-Review', 'code reviews') land on
    one set. Token-internal symbols (C#, CI/CD, .NET→net) are preserved.
    """
    tokens: set[str] = set()
    for raw in _norm(name).split():
        t = raw.strip(_SKILL_EDGE_PUNCT)
        if not t or t in _SKILL_STOPWORDS:
            continue
        tokens.add(_skill_stem(t))
    return frozenset(tokens)


# #308 (E049/US271, ADR-066/ADR-067) — shared-parenthetical-abbreviation shape.
# 'X (ABBR)' / 'Y (ABBR)' is a translation/synonym pair sharing its canonical
# abbreviation, e.g. the vault's 'MES (Manufacturing Execution System)' and a
# German writer's own 'Fertigungsleitsysteme (MES)'. Extracted from the RAW
# names (before ``_norm``/``_SKILL_EDGE_PUNCT`` fold case and unwrap parens —
# by the time a name reaches ``skill_tokens`` the parenthetical is just more
# bare, lowercased tokens, and the pairing is unrecoverable).
_PAREN_ABBR_RE = re.compile(r"\(([^()]*)\)")
_PAREN_ABBR_MIN_LEN = 2
_PAREN_ABBR_MAX_LEN = 6
_PAREN_ABBR_MIN_UPPER = 2


def _looks_like_abbreviation(segment: str) -> bool:
    """Shape guard for a candidate abbreviation segment (#308).

    A single token (no internal whitespace), 2–6 characters, with at least 2
    uppercase letters in its raw form. This is what separates a true abbreviation
    ('MES', 'GxP', 'CI/CD') from a qualifier ('Advanced': 8 chars, 1 uppercase) or
    a full expansion ('Manufacturing Execution System': not a single token).
    """
    token = segment.strip()
    if not token or any(ch.isspace() for ch in token):
        return False
    if not (_PAREN_ABBR_MIN_LEN <= len(token) <= _PAREN_ABBR_MAX_LEN):
        return False
    return sum(1 for ch in token if ch.isupper()) >= _PAREN_ABBR_MIN_UPPER


def _paren_abbreviation(name: str) -> str | None:
    """The abbreviation-shaped segment of a skill name of the form
    ``ABBR (Expansion)`` or ``Expansion (ABBR)`` (#308), or ``None`` when the
    name carries no parenthetical, or the shape is absent/ambiguous.

    Looks at BOTH the parenthetical content and the text outside it (the
    'head') — the abbreviation may sit on either side ('MES (Manufacturing
    Execution System)' has it in the head; 'Fertigungsleitsysteme (MES)' has
    it inside the parens). Exactly one of the two must pass the shape guard;
    if neither does (no abbreviation present) or both do (ambiguous — no way
    to tell which is canonical), this returns ``None`` rather than guess.
    """
    match = _PAREN_ABBR_RE.search(name)
    if match is None:
        return None
    inner = match.group(1)
    head = name[: match.start()] + name[match.end() :]
    candidates = [seg for seg in (inner, head) if _looks_like_abbreviation(seg)]
    if len(candidates) != 1:
        return None
    return candidates[0].strip()


def _shared_paren_abbreviation(a: str, b: str) -> bool:
    """True when both raw skill names carry a parenthetical abbreviation and the
    two abbreviations match case-insensitively (#308). One-sided parentheticals
    (only one name has any) are deliberately NOT handled here — they stay
    governed by the existing containment rules (:func:`skills_near_dupe`,
    :func:`skills_single_token_containment`). Symmetric by construction.
    """
    abbr_a = _paren_abbreviation(a)
    abbr_b = _paren_abbreviation(b)
    if abbr_a is None or abbr_b is None:
        return False
    return abbr_a.lower() == abbr_b.lower()


def skills_near_dupe(a: str, b: str) -> bool:
    """Are two skill names safe to AUTO-merge as the same skill? (#172, strict)

    True only when EITHER:

    * token-set containment where the *contained* side has ≥ 2 tokens — a modifier
      refinement of a real multi-word skill ('Team Leadership' ⊂ 'Team Leadership
      and Mentorship', 'GxP Compliance' ⊂ 'Regulatory Compliance … (GxP, CSV)'), OR
    * token overlap (Jaccard) reaches ``_NEAR_DUPE_JACCARD``, OR
    * both names carry a parenthetical abbreviation and the abbreviations match
      (#308, E049/US271, ADR-066/067) — a translation/synonym pair sharing its
      canonical abbreviation, e.g. 'MES (Manufacturing Execution System)' and
      'Fertigungsleitsysteme (MES)' (:func:`_shared_paren_abbreviation`). This is
      the ONE shared predicate five call sites rely on (cv.py, profile/reconcile/
      {import_bridge,dedupe,apply}.py, oracle/matchers/grounding.py) — fixed here
      once, never at a call site (ADR-066: doors are adapters).

    **Bare single-token containment is NOT a near-dupe.** One token strictly inside
    a larger set ('React' ⊂ 'React Native', 'Docker' ⊂ 'Docker & Kubernetes') names
    a *distinct* skill, and auto-merging would silently drop it or rename the atom
    into a compound (persisted corruption, UAT 2026-07-15). The reconciler routes
    such pairs to a user confirmation via :func:`skills_single_token_containment`.
    A ONE-SIDED parenthetical (only one name has any, e.g. 'MES' vs
    'Fertigungsleitsysteme (MES)') is untouched by the new disjunct and stays
    governed by this same containment rule.

    Token-level, so 'Java' ≠ 'JavaScript'. Symmetric; empty token sets never match.
    """
    if _shared_paren_abbreviation(a, b):
        return True
    ta, tb = skill_tokens(a), skill_tokens(b)
    if not ta or not tb:
        return False
    # Containment counts only when the contained (smaller/equal) side is itself a
    # multi-token name — never a bare single token inside a larger set.
    if ta <= tb and len(ta) >= 2:
        return True
    if tb <= ta and len(tb) >= 2:
        return True
    union = ta | tb
    return len(ta & tb) / len(union) >= _NEAR_DUPE_JACCARD


def skills_single_token_containment(a: str, b: str) -> bool:
    """Do two skill names relate ONLY by bare single-token containment? (#172)

    True when one token set is a *strict* subset of the other and the contained
    side is a single token — 'React' vs 'React Native', 'Docker' vs 'Docker &
    Kubernetes'. These are deliberately excluded from :func:`skills_near_dupe`
    (never auto-merged); the reconciler surfaces them as a user confirmation.
    Symmetric; empty token sets never match; equal sets are not containment.
    """
    ta, tb = skill_tokens(a), skill_tokens(b)
    if not ta or not tb:
        return False
    if ta < tb and len(ta) == 1:
        return True
    if tb < ta and len(tb) == 1:
        return True
    return False


def _entry_norms(entry: dict[str, Any]) -> set[str]:
    forms = entry.get("surface_forms") or [entry.get("concept", "")]
    return {_norm(f) for f in forms} | {_norm(entry.get("concept", ""))}


def keyword_present(keyword: str, text_norm: str, ledger: list[dict[str, Any]] | None = None) -> bool:
    """Presence per keyword = any of {keyword literal} ∪ owning entry surface_forms ∪ concept.

    Ownership honours the F4 gap stance: if any NON-claimable entry owns the keyword,
    only non-claimable owners widen the search — a foreign claimable entry's forms must
    never make an honest-gap keyword read as covered (ADR-048 §8 / #122).
    """
    k_norm = _norm(keyword)
    entries = ledger or []
    gap_owners = [e for e in entries if not e.get("claimable") and k_norm in _entry_norms(e)]
    owners = gap_owners or [e for e in entries if e.get("claimable") and k_norm in _entry_norms(e)]
    forms: list[str] = [keyword]
    for e in owners:
        forms.extend(e.get("surface_forms") or [])
        if e.get("concept"):
            forms.append(e["concept"])
    return any(surface_present(f, text_norm) for f in forms)


def _years(date_str: str | None) -> list[str]:
    return re.findall(r"\d{4}", date_str or "")


def _check(checks: list[ATSCheck], cid: str, ok: bool, details: str | None = None) -> None:
    checks.append(ATSCheck(id=cid, status="pass" if ok else "fail", details=None if ok else details))


def _keyword_coverage(
    text_norm: str,
    keywords: list[str],
    ledger: list[dict[str, Any]] | None = None,
    vault_text_norm: str | None = None,
) -> ATSKeywordCoverage:
    seen: set[str] = set()
    unique: list[str] = []
    for k in keywords:
        if k and k.lower() not in seen:
            seen.add(k.lower())
            unique.append(k)
    # US212 (#122): presence via the shared predicate — surface-form union over the
    # keyword's owning ledger entry plus the morphological fold, not the literal alone.
    present = [k for k in unique if keyword_present(k, text_norm, ledger)]
    missing = [k for k in unique if k not in set(present)]

    # US203 (ADR-048): split missing into "claimable" (the candidate supports it per the
    # ledger — a surfacing miss) vs "honest gap" (not in the profile). No ledger → all
    # missing are honest gaps (back-compat; never silently claimable). The audit stays
    # deterministic and local — no LLM, no synthetic score.
    from applire.services.keyword_ledger import (
        claimable_surface_forms,
        keyword_liabilities,
        unsupported_claim_surface_forms,
    )

    claimable_concepts = claimable_surface_forms(ledger)
    claimable_norm = {_norm(f) for f in claimable_concepts}
    missing_claimable = [k for k in missing if _norm(k) in claimable_norm]
    missing_honest_gap = [k for k in missing if _norm(k) not in claimable_norm]

    # ADR-048 amended 2026-07-03 (#117), fourth quadrant: a PRESENT keyword the ledger
    # marks unsupported (honest gap) is a truthfulness warning — it reached the document
    # without profile evidence (e.g. typed in via the section editor). Claimable always
    # wins on alias collisions; without a ledger we cannot judge, so nothing is flagged.
    # ADR-048/059 am. 2026-07-27: scoped to UNKNOWN gaps — a `denied` concept named
    # in an honest negation is not an unsupported claim, and this predicate cannot
    # see negation. The direction-aware check lives with the Oracle.
    unclaimable_norm = {_norm(f) for f in unsupported_claim_surface_forms(ledger)}
    # #249 run-4 (2026-07-24): ONE shared presence predicate over BOTH
    # surfaces — a keyword that clears `surface_present` against the vault's
    # OWN literal text (the same instrument the Truthfulness Oracle's
    # ground_skill_claim checks, services/oracle/matchers/grounding.py) can
    # never be flagged present_unsupported, whatever the Keyword Ledger's own
    # classification says. Defense-in-depth over narrowing the denial match
    # at ledger-build time (services/keyword_ledger.py): even a stale or
    # otherwise mis-classified ledger row can no longer put the ATS panel and
    # the Oracle at odds about the same skill string. `vault_text_norm` is
    # optional and back-compat (None for every existing caller) — omitting it
    # reproduces today's behaviour exactly.
    literally_grounded = (
        {k for k in present if surface_present(k, vault_text_norm)}
        if vault_text_norm
        else set()
    )
    present_unsupported = [
        k for k in present
        if _norm(k) in unclaimable_norm
        and _norm(k) not in claimable_norm
        and k not in literally_grounded
    ]
    # E048/US266 (#249 option b): the FULL claimable list, independent of
    # presence — the same list already computed above for the missing_claimable
    # split, exposed on the report itself.
    # #260: the pre-generation liability slice, exposed on the report too (agent
    # + report-surface parity) — orthogonal to claimable_concepts (literal vault
    # presence) and independent of document presence, same as claimable_concepts.
    keyword_liability_concepts = [e.get("concept", "") for e in keyword_liabilities(ledger)]
    return ATSKeywordCoverage(
        present=present,
        missing=missing,
        missing_claimable=missing_claimable,
        missing_honest_gap=missing_honest_gap,
        present_unsupported=present_unsupported,
        claimable_concepts=claimable_concepts,
        keyword_liability_concepts=keyword_liability_concepts,
    )


def _finish(document: Literal["cv", "cover_letter"], checks: list[ATSCheck], coverage: ATSKeywordCoverage) -> ATSReport:
    return ATSReport(
        document=document,
        checks=checks,
        keywords=coverage,
        passed=sum(1 for c in checks if c.status == "pass"),
        failed=sum(1 for c in checks if c.status == "fail"),
    )


def _audit_cv_text(
    text: str,
    tailored: TailoredCVData,
    keywords: list[str],
    ledger: list[dict[str, Any]] | None = None,
    page_count: int | None = None,
    target: int | None = None,
    region: str = DEFAULT_REGION,
    condensation_exhausted: bool = False,
    vault_text_norm: str | None = None,
) -> ATSReport:
    t = _norm(text)
    checks: list[ATSCheck] = []

    c = tailored.contact
    if c.name:
        _check(checks, "contact-name", _find(c.name, t) >= 0, f"name '{c.name}' not found in extracted text")
    if c.email:
        _check(checks, "contact-email", _find(c.email, t) >= 0, f"email '{c.email}' not found")
    if c.phone:
        digits = re.sub(r"\D", "", c.phone)
        _check(checks, "contact-phone", digits in re.sub(r"\D", "", text), f"phone '{c.phone}' not found")

    entry_positions: list[int] = []
    for i, w in enumerate(tailored.work_history):
        company_norm = _norm(w.company)
        role_norm = _norm(w.role)
        # Skip the check entirely when BOTH fields are empty
        if not company_norm and not role_norm:
            continue
        pos_company = _find(w.company, t) if company_norm else None
        pos_role = _find(w.role, t) if role_norm else None
        years_ok = all(y in text for y in _years(w.start_date))
        # Each non-empty field must be present; empty fields are not required
        company_ok = pos_company is None or pos_company >= 0
        role_ok = pos_role is None or pos_role >= 0
        ok = company_ok and role_ok and years_ok
        _check(checks, f"work-{i}", ok,
               f"entry '{w.role} @ {w.company}' incomplete in extracted text "
               f"(company={'ok' if company_ok else 'missing'}, role={'ok' if role_ok else 'missing'}, "
               f"year={'ok' if years_ok else 'missing'})")
        # Use whichever position is available for reading-order tracking
        anchor = pos_company if pos_company is not None else (pos_role if pos_role is not None else -1)
        entry_positions.append(anchor)

    if len(entry_positions) > 1 and all(p >= 0 for p in entry_positions):
        ordered = all(a <= b for a, b in zip(entry_positions, entry_positions[1:]))
        _check(checks, "reading-order", ordered,
               "work-history entries appear in a different order in the extracted text "
               "than in the CV data (each entry anchored at its first occurrence of "
               "company/role text)")

    for i, e in enumerate(tailored.education):
        institution_norm = _norm(e.institution)
        degree_norm = _norm(e.degree)
        # Skip entirely if both fields are empty
        if not institution_norm and not degree_norm:
            continue
        institution_ok = not institution_norm or _find(e.institution, t) >= 0
        degree_ok = not degree_norm or _find(e.degree, t) >= 0
        ok = institution_ok and degree_ok
        _check(checks, f"education-{i}", ok, f"education entry '{e.degree} {e.institution}' not fully found")

    if tailored.skills:
        missing_skills = [s for s in tailored.skills if _find(s, t) < 0]
        _check(checks, "skills", not missing_skills,
               "skills missing from extracted text: " + ", ".join(missing_skills))

        # #172: near-duplicate skill tags in the rendered CV (belt-and-braces over
        # the render-side dedup — the SAME shared predicate).
        near_pairs = [
            (a, b)
            for i, a in enumerate(tailored.skills)
            for b in tailored.skills[i + 1:]
            if skills_near_dupe(a, b)
        ]
        _check(checks, "skills-near-dupe", not near_pairs,
               "near-duplicate skills: " + "; ".join(f"'{a}' ~ '{b}'" for a, b in near_pairs))

    # #169: a role bullet repeated inside a project nested under that role (belt-and-
    # braces over the deterministic suppression in cv._nest_projects). Only emitted
    # when there is at least one nested project to compare.
    if any((w.projects or []) for w in tailored.work_history):
        collisions: list[str] = []
        for w in tailored.work_history:
            role_norms = {_norm(b) for b in (w.bullets or []) if b and _norm(b)}
            for proj in (w.projects or []):
                for pb in (proj.bullets or []):
                    if pb and _norm(pb) in role_norms:
                        collisions.append(pb)
        _check(checks, "duplicate-bullets", not collisions,
               "bullets duplicated between a role and its nested project: "
               + "; ".join(f"'{b}'" for b in collisions))

    # E042/US238 (ADR-051 §5 + amendment §3): target-aware page-length band, replacing
    # the #171a fixed 2/3 thresholds. ATSCheck has no "warn" status, so anything up to
    # the region max passes (carrying an advisory detail when it deviates from the
    # region standard); only over the max fails. Skipped when no count is given
    # (text-only callers/tests). All norm numbers come from REGION_NORMS — never
    # hard-code a page number (ADR-051 §1). Keep id "page-length" (frontend i18n keys
    # on it); details carry a details_key + details_params pair so the frontend can
    # localise them (ADR-038), with the EN `details` string as the fallback.
    #
    # #238 (founder-acceptance F4) amendment: the "within max" branch used to pass
    # unconditionally once page_count <= maximum, regardless of WHY it was over
    # tgt — so an EXPLICIT target the candidate chose (production always resolves
    # and passes one; only text-only callers omit it) that the condense loop
    # could not hit still shipped as unprompted "senior profile" advice, hiding
    # the miss. `target is not None` distinguishes "a target was actually asked
    # for" from the back-compat "no target given, defaults to the regional
    # standard" callers — only the latter keeps the old blanket senior-advisory
    # wording; the former is now an honest fail, whether or not
    # `condensation_exhausted` was set (the section-editor re-audit path can miss
    # the target without ever running the condense loop at all — same honesty
    # applies defensively).
    if page_count is not None:
        norm = REGION_NORMS[region]
        standard = norm.cv_standard_pages
        maximum = norm.cv_max_pages
        tgt = target if target is not None else standard
        if page_count <= standard:
            # Within the regional norm — always a plain pass, whatever the target.
            # Guards both directions: a higher chosen target that wasn't needed
            # (no deviation, no noise) AND a sub-standard target (MCP-reachable)
            # that must not trigger the senior wording on a norm-conforming doc.
            checks.append(ATSCheck(id="page-length", status="pass", details=None))
        elif page_count <= tgt:
            # The chosen target was actually USED to go beyond the norm — advise.
            checks.append(ATSCheck(
                id="page-length", status="pass",
                details=f"{page_count} pages — meets your chosen target of {tgt}; "
                        f"the {region} norm is {standard} pages",
                details_key="page-length-target",
                details_params={"pages": page_count, "target": tgt,
                                "region": region, "standard": standard},
            ))
        elif page_count <= maximum and target is None:
            # No explicit target was asked for (back-compat: the region standard
            # was used as a default, not a candidate choice) — the old advisory
            # wording is still honest here, nothing was promised and missed.
            checks.append(ATSCheck(
                id="page-length", status="pass",
                details=f"{page_count} pages — acceptable for senior profiles; "
                        f"the {region} norm is {standard} pages",
                details_key="page-length-senior",
                details_params={"pages": page_count, "region": region, "standard": standard},
            ))
        elif page_count <= maximum:
            # #238: an explicit target was chosen and missed, even though the
            # document still sits within the regional max — must fail honestly,
            # never dressed up as senior-profile advice (founder-acceptance F4).
            checks.append(ATSCheck(
                id="page-length", status="fail",
                details=f"{page_count} pages — couldn't condense to your {tgt}-page "
                        f"target without cutting relevant content; the {region} max "
                        f"is {maximum} pages",
                details_key="page-length-target-missed",
                details_params={"pages": page_count, "target": tgt, "region": region,
                                "standard": standard, "max": maximum},
            ))
        elif condensation_exhausted:
            checks.append(ATSCheck(
                id="page-length", status="fail",
                details=f"{page_count} pages — condensed to the maximum; length driven by "
                        f"education/skills volume; exceeds the {region} norm of {standard} "
                        f"pages (max {maximum})",
                details_key="page-length-exhausted",
                details_params={"pages": page_count, "region": region,
                                "standard": standard, "max": maximum},
            ))
        else:
            checks.append(ATSCheck(
                id="page-length", status="fail",
                details=f"{page_count} pages — exceeds the {region} norm of {standard} "
                        f"pages (max {maximum})",
                details_key="page-length-exceeds",
                details_params={"pages": page_count, "region": region,
                                "standard": standard, "max": maximum},
            ))

    return _finish("cv", checks, _keyword_coverage(t, keywords, ledger, vault_text_norm))


def audit_cv(
    pdf_bytes: bytes,
    tailored: TailoredCVData,
    keywords: list[str],
    ledger: list[dict[str, Any]] | None = None,
    target: int | None = None,
    region: str = DEFAULT_REGION,
    condensation_exhausted: bool = False,
    vault_text_norm: str | None = None,
) -> ATSReport:
    """Audit a rendered CV PDF against the structured CV data and a list of keywords.

    NOTE (E042/US238): production no longer calls this — the CV pipeline's condense
    loop needs the page count itself, so ``services/cv._update_ats_report`` extracts
    once via :func:`extract_text_and_pages` and audits via :func:`_audit_cv_text`
    directly. This PDF-level convenience wrapper is kept as the entry point for the
    ADR-039 render-roundtrip harness (``tests/ats/test_roundtrip.py``) and unit tests;
    keep its behaviour in lockstep with the production pair above.

    ``ledger`` (the Keyword Ledger, ADR-048/US203) annotates each MISSING keyword as
    *missing-claimable* (supported by the profile per the ledger) vs *missing-honest-gap*.

    ``target``/``region``/``condensation_exhausted`` (E042/US238, ADR-051 §5) drive the
    target-aware page-length band; ``target`` defaults to the region standard.

    ``vault_text_norm`` (#249 run-4, 2026-07-24): optional normalised literal vault
    text — when given, a keyword that clears THE shared presence predicate against
    it can never land in ``present_unsupported``, regardless of the ledger's own
    classification (defense-in-depth over the denial-narrowing fix in
    ``services/keyword_ledger.py``). ``None`` (default) reproduces prior behaviour.
    """
    text, page_count = extract_text_and_pages(pdf_bytes)
    return _audit_cv_text(
        text, tailored, keywords, ledger, page_count=page_count,
        target=target, region=region, condensation_exhausted=condensation_exhausted,
        vault_text_norm=vault_text_norm,
    )


def _audit_letter_text(
    text: str,
    letter_data: dict[str, Any],
    keywords: list[str],
    ledger: list[dict[str, Any]] | None = None,
    page_count: int | None = None,
    vault_text_norm: str | None = None,
) -> ATSReport:
    t = _norm(text)
    checks: list[ATSCheck] = []

    header = letter_data.get("header") or {}
    if header.get("name"):
        _check(checks, "contact-name", _find(header["name"], t) >= 0, f"name '{header['name']}' not found")
    if header.get("email"):
        _check(checks, "contact-email", _find(header["email"], t) >= 0, f"email '{header['email']}' not found")

    recipient = letter_data.get("recipient") or {}
    if recipient.get("company"):
        _check(checks, "recipient-company", _find(recipient["company"], t) >= 0,
               f"recipient company '{recipient['company']}' not found")

    paragraphs = (letter_data.get("body") or {}).get("paragraphs") or []
    for i, p in enumerate(paragraphs):
        probe = p[:60]
        if not _norm(probe):
            continue  # empty/whitespace paragraph — nothing to verify (mirrors the CV-side empty-field guard)
        _check(checks, f"body-{i}", _find(probe, t) >= 0, f"body paragraph {i + 1} not found in extracted text")

    # E042/US240 (ADR-051 §6): DETECTION-ONLY page-length check against the region's
    # 1-page letter norm — deliberately no target resolution, no user setting, no
    # condense loop for letters this flavour (unlike the CV band in _audit_cv_text).
    # Same check id ("page-length") as the CV check — the frontend ATSChecksPanel and
    # the checks.page-length i18n key are shared by both document types. Skipped when
    # no count is given (text-only callers/tests), mirroring the CV behaviour. The
    # norm number always comes from REGION_NORMS — never hard-coded (ADR-051 §1).
    if page_count is not None:
        region = DEFAULT_REGION
        letter_pages = REGION_NORMS[region].letter_pages
        if page_count <= letter_pages:
            checks.append(ATSCheck(id="page-length", status="pass", details=None))
        else:
            checks.append(ATSCheck(
                id="page-length", status="fail",
                details=f"{page_count} pages — a {region} cover letter is {letter_pages} page",
                details_key="page-length-letter",
                details_params={"pages": page_count, "region": region,
                                "letterPages": letter_pages},
            ))

    return _finish("cover_letter", checks, _keyword_coverage(t, keywords, ledger, vault_text_norm))


def audit_cover_letter(
    pdf_bytes: bytes,
    letter_data: dict[str, Any],
    keywords: list[str],
    ledger: list[dict[str, Any]] | None = None,
    vault_text_norm: str | None = None,
) -> ATSReport:
    """Audit a rendered cover letter PDF against the structured letter data and keywords.

    ``ledger`` (ADR-048/US203) splits each MISSING keyword into *missing-claimable* vs
    *missing-honest-gap*.

    E042/US240: reads the real page count via :func:`extract_text_and_pages` (one
    PdfReader pass, #171a-style) and threads it into :func:`_audit_letter_text` for
    the detection-only page-length check.

    ``vault_text_norm`` (#249 run-4): see :func:`audit_cv` — same shared-predicate
    guard on ``present_unsupported``, optional and back-compat.
    """
    text, page_count = extract_text_and_pages(pdf_bytes)
    return _audit_letter_text(
        text, letter_data, keywords, ledger, page_count=page_count,
        vault_text_norm=vault_text_norm,
    )
