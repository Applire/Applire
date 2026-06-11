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
from io import BytesIO

from pypdf import PdfReader

from applire.schemas.ats import ATSCheck, ATSKeywordCoverage, ATSReport
from applire.schemas.cv import TailoredCVData


def extract_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).lower().strip()


def _find(needle: str, haystack_norm: str) -> int:
    """First index of normalised needle in pre-normalised haystack; -1 if absent."""
    n = _norm(needle)
    return haystack_norm.find(n) if n else 0


def _years(date_str: str | None) -> list[str]:
    return re.findall(r"\d{4}", date_str or "")


def _check(checks: list[ATSCheck], cid: str, ok: bool, details: str | None = None) -> None:
    checks.append(ATSCheck(id=cid, status="pass" if ok else "fail", details=None if ok else details))


def _keyword_coverage(text_norm: str, keywords: list[str]) -> ATSKeywordCoverage:
    present = [k for k in keywords if _find(k, text_norm) >= 0]
    missing = [k for k in keywords if k not in present]
    return ATSKeywordCoverage(present=present, missing=missing)


def _finish(document: str, checks: list[ATSCheck], coverage: ATSKeywordCoverage) -> ATSReport:
    return ATSReport(
        document=document,  # type: ignore[arg-type]
        checks=checks,
        keywords=coverage,
        passed=sum(1 for c in checks if c.status == "pass"),
        failed=sum(1 for c in checks if c.status == "fail"),
    )


def _audit_cv_text(text: str, tailored: TailoredCVData, keywords: list[str]) -> ATSReport:
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
        pos_company = _find(w.company, t)
        pos_role = _find(w.role, t)
        years_ok = all(y in text for y in _years(w.start_date))
        ok = pos_company >= 0 and pos_role >= 0 and years_ok
        _check(checks, f"work-{i}", ok,
               f"entry '{w.role} @ {w.company}' incomplete in extracted text "
               f"(company={'ok' if pos_company >= 0 else 'missing'}, role={'ok' if pos_role >= 0 else 'missing'}, "
               f"year={'ok' if years_ok else 'missing'})")
        entry_positions.append(pos_company)

    if len(entry_positions) > 1 and all(p >= 0 for p in entry_positions):
        ordered = all(a <= b for a, b in zip(entry_positions, entry_positions[1:]))
        _check(checks, "reading-order", ordered,
               "work-history entries appear out of order in the extracted text (column interleaving?)")

    for i, e in enumerate(tailored.education):
        ok = _find(e.institution, t) >= 0 and _find(e.degree, t) >= 0
        _check(checks, f"education-{i}", ok, f"education entry '{e.degree} {e.institution}' not fully found")

    if tailored.skills:
        missing_skills = [s for s in tailored.skills if _find(s, t) < 0]
        _check(checks, "skills", not missing_skills,
               "skills missing from extracted text: " + ", ".join(missing_skills))

    return _finish("cv", checks, _keyword_coverage(t, keywords))


def audit_cv(pdf_bytes: bytes, tailored: TailoredCVData, keywords: list[str]) -> ATSReport:
    return _audit_cv_text(extract_text(pdf_bytes), tailored, keywords)


def _audit_letter_text(text: str, letter_data: dict, keywords: list[str]) -> ATSReport:
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
        _check(checks, f"body-{i}", _find(probe, t) >= 0, f"body paragraph {i + 1} not found in extracted text")

    return _finish("cover_letter", checks, _keyword_coverage(t, keywords))


def audit_cover_letter(pdf_bytes: bytes, letter_data: dict, keywords: list[str]) -> ATSReport:
    return _audit_letter_text(extract_text(pdf_bytes), letter_data, keywords)
