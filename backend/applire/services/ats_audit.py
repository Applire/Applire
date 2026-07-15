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


def surface_present(form: str, text_norm: str) -> bool:
    """THE presence predicate (US212): is this surface form in this normalised text?

    Single shared instrument for the ATS panel, the gap hints (#117), and the
    generation-time coverage check (US213) — consumers may never disagree on
    presence by construction (ADR-048 amended 2026-07-04, #122).
    """
    n = _norm(form)
    if not n:
        return False
    return any(text_norm.find(v) >= 0 for v in _fold_variants(n))


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
    "s" only when the stem keeps ≥ ``_FOLD_MIN_STEM`` chars (never "SaaS" → "saa")."""
    if token.endswith("s") and len(token) - 1 >= _FOLD_MIN_STEM:
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


def skills_near_dupe(a: str, b: str) -> bool:
    """Do two skill names denote the same (or a strict-subset) skill? (#172)

    True when one token set is a subset of the other — a modifier refinement
    ('Team Leadership' ⊂ 'Team Leadership and Mentorship') or a compound naming an
    atom ('Docker' ⊂ 'Cloud Infrastructure & Deployment (Docker Compose)') — OR
    when token overlap (Jaccard) reaches ``_NEAR_DUPE_JACCARD``. Token-level, so
    'Java' ≠ 'JavaScript' and 'Python' ≠ 'TypeScript' (distinct single tokens, no
    containment). Symmetric; empty token sets never match.
    """
    ta, tb = skill_tokens(a), skill_tokens(b)
    if not ta or not tb:
        return False
    if ta <= tb or tb <= ta:
        return True
    union = ta | tb
    return len(ta & tb) / len(union) >= _NEAR_DUPE_JACCARD


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
        unclaimable_surface_forms,
    )

    claimable_norm = {_norm(f) for f in claimable_surface_forms(ledger)}
    missing_claimable = [k for k in missing if _norm(k) in claimable_norm]
    missing_honest_gap = [k for k in missing if _norm(k) not in claimable_norm]

    # ADR-048 amended 2026-07-03 (#117), fourth quadrant: a PRESENT keyword the ledger
    # marks unsupported (honest gap) is a truthfulness warning — it reached the document
    # without profile evidence (e.g. typed in via the section editor). Claimable always
    # wins on alias collisions; without a ledger we cannot judge, so nothing is flagged.
    unclaimable_norm = {_norm(f) for f in unclaimable_surface_forms(ledger)}
    present_unsupported = [
        k for k in present
        if _norm(k) in unclaimable_norm and _norm(k) not in claimable_norm
    ]
    return ATSKeywordCoverage(
        present=present,
        missing=missing,
        missing_claimable=missing_claimable,
        missing_honest_gap=missing_honest_gap,
        present_unsupported=present_unsupported,
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

    # #171a: DACH page-length policy. ATSCheck has no "warn" status, so 3 pages
    # passes but carries an advisory detail; > 3 fails. Skipped when no count given
    # (text-only callers/tests).
    if page_count is not None:
        if page_count <= 2:
            checks.append(ATSCheck(id="page-length", status="pass", details=None))
        elif page_count == 3:
            checks.append(ATSCheck(
                id="page-length", status="pass",
                details="3 pages — acceptable for senior profiles; the DACH norm is 2 pages",
            ))
        else:
            checks.append(ATSCheck(
                id="page-length", status="fail",
                details=f"{page_count} pages — exceeds the DACH norm of 2 pages (max 3)",
            ))

    return _finish("cv", checks, _keyword_coverage(t, keywords, ledger))


def audit_cv(
    pdf_bytes: bytes,
    tailored: TailoredCVData,
    keywords: list[str],
    ledger: list[dict[str, Any]] | None = None,
) -> ATSReport:
    """Audit a rendered CV PDF against the structured CV data and a list of keywords.

    ``ledger`` (the Keyword Ledger, ADR-048/US203) annotates each MISSING keyword as
    *missing-claimable* (supported by the profile per the ledger) vs *missing-honest-gap*.
    """
    text, page_count = extract_text_and_pages(pdf_bytes)
    return _audit_cv_text(text, tailored, keywords, ledger, page_count=page_count)


def _audit_letter_text(
    text: str,
    letter_data: dict[str, Any],
    keywords: list[str],
    ledger: list[dict[str, Any]] | None = None,
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

    return _finish("cover_letter", checks, _keyword_coverage(t, keywords, ledger))


def audit_cover_letter(
    pdf_bytes: bytes,
    letter_data: dict[str, Any],
    keywords: list[str],
    ledger: list[dict[str, Any]] | None = None,
) -> ATSReport:
    """Audit a rendered cover letter PDF against the structured letter data and keywords.

    ``ledger`` (ADR-048/US203) splits each MISSING keyword into *missing-claimable* vs
    *missing-honest-gap*.
    """
    return _audit_letter_text(extract_text(pdf_bytes), letter_data, keywords, ledger)
