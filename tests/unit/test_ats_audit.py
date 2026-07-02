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

from applire.schemas.cv import TailoredCVData
from applire.services.ats_audit import _audit_cv_text, _audit_letter_text

_CV = TailoredCVData.model_validate({
    "contact": {"name": "Anna Bauer", "email": "anna@example.com", "phone": "+49 151 1234567", "location": "Berlin"},
    "summary": "Backend engineer with cloud focus.",
    "work_history": [
        {"company": "Cloudwerk GmbH", "role": "Senior Backend Engineer", "start_date": "2021-04", "end_date": None,
         "bullets": ["Built FastAPI services", "Led Kubernetes migration"]},
        {"company": "DataHaus AG", "role": "Software Engineer", "start_date": "2017-09", "end_date": "2021-03",
         "bullets": ["Maintained ETL pipelines"]},
    ],
    "skills": ["Python", "FastAPI", "Kubernetes"],
    "education": [{"institution": "TU Berlin", "degree": "M.Sc.", "field": "Informatik", "start_date": "2014-10", "end_date": "2017-08"}],
    "languages": [{"language": "Deutsch", "level": "C2"}],
})

def _full_text() -> str:
    return (
        "Anna Bauer anna@example.com +49 151 1234567 Berlin\n"
        "Senior Backend Engineer Cloudwerk GmbH 04/2021 - heute\n"
        "Software Engineer DataHaus AG 09/2017 - 03/2021\n"
        "Python FastAPI Kubernetes\n"
        "TU Berlin M.Sc. Informatik 2014 - 2017\n"
    )

def test_all_checks_pass_on_faithful_text():
    report = _audit_cv_text(_full_text(), _CV, keywords=["Python", "GraphQL"])
    assert report.failed == 0
    assert report.document == "cv"
    assert report.keywords.present == ["Python"]
    assert report.keywords.missing == ["GraphQL"]

def test_missing_contact_and_entry_fail():
    text = "Senior Backend Engineer Cloudwerk GmbH 2021"
    report = _audit_cv_text(text, _CV, keywords=[])
    failed_ids = {c.id for c in report.checks if c.status == "fail"}
    assert "contact-name" in failed_ids
    assert "work-1" in failed_ids          # DataHaus entry absent

def test_reading_order_fails_when_entries_swapped():
    text = (
        "Anna Bauer anna@example.com +49 151 1234567\n"
        "Software Engineer DataHaus AG 2017 2021\n"
        "Senior Backend Engineer Cloudwerk GmbH 2021\n"
        "Python FastAPI Kubernetes TU Berlin M.Sc. Informatik 2014 2017\n"
    )
    report = _audit_cv_text(text, _CV, keywords=[])
    assert any(c.id == "reading-order" and c.status == "fail" for c in report.checks)

def test_year_only_date_matching():
    report = _audit_cv_text(_full_text().replace("04/2021", "April 2021"), _CV, keywords=[])
    assert report.failed == 0

def test_checks_skipped_for_absent_data():
    cv = _CV.model_copy(update={"contact": _CV.contact.model_copy(update={"email": None, "phone": None})})
    text = "Anna Bauer Senior Backend Engineer Cloudwerk GmbH 2021 Software Engineer DataHaus AG 2017 Python FastAPI Kubernetes TU Berlin M.Sc. Informatik 2014"
    report = _audit_cv_text(text, cv, keywords=[])
    ids = {c.id for c in report.checks}
    assert "contact-email" not in ids and "contact-phone" not in ids

def test_letter_audit():
    letter = {
        "header": {"name": "Anna Bauer", "email": "anna@example.com", "phone": None, "address": "Berlin"},
        "recipient": {"company": "Cloudwerk GmbH", "name": "Herr Schmidt", "title": None, "address": None, "date": "11. Juni 2026"},
        "body": {"paragraphs": ["Sehr geehrter Herr Schmidt,", "ich bewerbe mich…", "Mit freundlichen Grüßen"]},
        "signature": {"name": "Anna Bauer"},
    }
    text = "Anna Bauer anna@example.com Berlin Cloudwerk GmbH Herr Schmidt Sehr geehrter Herr Schmidt, ich bewerbe mich… Mit freundlichen Grüßen"
    report = _audit_letter_text(text, letter, keywords=["Cloud"])
    assert report.document == "cover_letter"
    assert report.failed == 0


# ---------------------------------------------------------------------------
# Fix 1: empty-field guards
# ---------------------------------------------------------------------------

def test_empty_fields_do_not_false_pass():
    """Fix 1: empty company/role should not silently pass; real role not in text must FAIL."""
    from applire.schemas.cv import TailoredCVData

    # Work entry with empty company but real role "Engineer" that is NOT in the text
    cv = TailoredCVData.model_validate({
        "contact": {"name": "Test User", "email": None, "phone": None, "location": None},
        "summary": "",
        "work_history": [
            {"company": "", "role": "Engineer", "start_date": None, "end_date": None, "bullets": []},
        ],
        "skills": [],
        "education": [],
        "languages": [],
    })
    # Text does NOT contain "Engineer"
    text = "Test User some unrelated text"
    report = _audit_cv_text(text, cv, keywords=[])
    failed_ids = {c.id for c in report.checks if c.status == "fail"}
    assert "work-0" in failed_ids, "work entry with real role not in text must FAIL, not silently pass"

    # Work entry with BOTH company="" and role="" → no check emitted at all
    cv_both_empty = TailoredCVData.model_validate({
        "contact": {"name": "Test User", "email": None, "phone": None, "location": None},
        "summary": "",
        "work_history": [
            {"company": "", "role": "", "start_date": None, "end_date": None, "bullets": []},
        ],
        "skills": [],
        "education": [],
        "languages": [],
    })
    report2 = _audit_cv_text("Test User", cv_both_empty, keywords=[])
    ids = {c.id for c in report2.checks}
    assert "work-0" not in ids, "work entry with both company and role empty must emit no check"


def test_empty_keyword_not_counted_present():
    """Fix 1: empty string keyword must not appear in present or missing."""
    report = _audit_cv_text(_full_text(), _CV, keywords=[""])
    assert report.keywords.present == [], "empty keyword must not appear as present"
    assert report.keywords.missing == [], "empty keyword must not appear as missing"


# ---------------------------------------------------------------------------
# Fix 2: Unicode robustness
# ---------------------------------------------------------------------------

def test_unicode_extraction_variants():
    """Fix 2: decomposed umlauts, soft hyphens, and ligatures must all match."""
    import unicodedata
    from applire.schemas.cv import TailoredCVData

    # Decomposed "Müller": M + u + combining diaeresis + ller
    decomposed_mueller = "Müller"
    assert decomposed_mueller != "Müller"  # confirm they differ at the string level

    cv_mueller = TailoredCVData.model_validate({
        "contact": {"name": "Jörg Müller", "email": None, "phone": None, "location": None},
        "summary": "",
        "work_history": [],
        "skills": [],
        "education": [],
        "languages": [],
    })
    # Text contains decomposed form of the name
    text_decomposed = f"Jörg Müller"
    report = _audit_cv_text(text_decomposed, cv_mueller, keywords=[])
    assert all(c.status == "pass" for c in report.checks), \
        f"decomposed umlaut in text should still match; checks: {report.checks}"

    # Soft hyphen in text: "Pro­fil" (U+00AD between o and f)
    cv_profil = TailoredCVData.model_validate({
        "contact": {"name": "Profil Expert", "email": None, "phone": None, "location": None},
        "summary": "",
        "work_history": [],
        "skills": [],
        "education": [],
        "languages": [],
    })
    text_softhyphen = "Pro­fil Expert"
    report2 = _audit_cv_text(text_softhyphen, cv_profil, keywords=[])
    assert all(c.status == "pass" for c in report2.checks), \
        f"soft hyphen in text should be stripped before matching; checks: {report2.checks}"

    # Ligature fi (U+FB01): "Proﬁ" (P-r-o-U+FB01) in text, needle is "Profi"
    cv_profi = TailoredCVData.model_validate({
        "contact": {"name": "Profi Engineer", "email": None, "phone": None, "location": None},
        "summary": "",
        "work_history": [],
        "skills": [],
        "education": [],
        "languages": [],
    })
    text_ligature = "Proﬁ Engineer"  # U+FB01 = ﬁ ligature → NFKC → "fi"
    report3 = _audit_cv_text(text_ligature, cv_profi, keywords=[])
    assert all(c.status == "pass" for c in report3.checks), \
        f"fi-ligature in text should expand to 'fi' before matching; checks: {report3.checks}"


# ---------------------------------------------------------------------------
# Fix 3: keyword de-duplication
# ---------------------------------------------------------------------------

def test_duplicate_keywords_deduplicated():
    """Fix 3: duplicate keywords (case-insensitive) should appear only once in present."""
    report = _audit_cv_text(_full_text(), _CV, keywords=["Python", "python", "Python"])
    assert report.keywords.present == ["Python"], \
        f"duplicates must be collapsed to one entry; got {report.keywords.present}"


# ---------------------------------------------------------------------------
# E037 US203: missing keywords split into claimable vs honest-gap
# ---------------------------------------------------------------------------

# A ledger where "Python" is held (claimable), "GraphQL" is an honest gap.
_LEDGER = [
    {"concept": "Python", "surface_forms": ["Python"], "claimable": True,
     "status": "direct", "sources": ["required"], "fit_weight": 1.0, "evidence": "5y"},
    {"concept": "GraphQL", "surface_forms": ["GraphQL"], "claimable": False,
     "status": "gap", "sources": ["required"], "fit_weight": 1.0, "evidence": ""},
]


def test_missing_keywords_split_into_claimable_and_honest_gap():
    """US203: a missing keyword the candidate HAS per the ledger (claimable) is a
    surfacing miss; a missing keyword they don't have is an honest gap. Present
    keywords never appear in either bucket."""
    # Text contains neither Python nor GraphQL → both missing, but bucketed differently.
    text = "Anna Bauer some unrelated prose with no job keywords"
    report = _audit_cv_text(text, _CV, keywords=["Python", "GraphQL"], ledger=_LEDGER)
    assert set(report.keywords.missing) == {"Python", "GraphQL"}        # back-compat list intact
    assert report.keywords.missing_claimable == ["Python"]              # held but absent → fixable
    assert report.keywords.missing_honest_gap == ["GraphQL"]            # not in profile → honest


def test_present_keyword_not_in_either_missing_bucket():
    report = _audit_cv_text(_full_text(), _CV, keywords=["Python", "GraphQL"], ledger=_LEDGER)
    assert report.keywords.present == ["Python"]
    assert "Python" not in report.keywords.missing_claimable
    assert report.keywords.missing_honest_gap == ["GraphQL"]


def test_denied_gap_keyword_aliased_by_claimable_entry_is_honest_gap():
    """F4 (blind PQ 2026-07-02, trust-critical): the gap LLM echoed 'Azure' as a
    surface form of the claimable compound requirement 'Cloud environment
    qualification (AWS, Azure)' while classifying 'Azure' itself as an honest gap
    in the SAME ledger. The audit then bucketed the missing keyword 'Azure' as
    missing_claimable → the panel read 'Supported by your profile' although the
    user had denied Azure experience. Through the REAL builder, 'Azure' must land
    in missing_honest_gap."""
    from applire.services.keyword_ledger import build_keyword_ledger

    ledger = build_keyword_ledger(
        classifications=[
            {
                "concept": "Cloud environment qualification (AWS, Azure)",
                "status": "partial",
                "surface_forms": ["Cloud environment qualification", "AWS", "Azure"],
                "evidence": "Qualified first GxP cloud environment (AWS). Azure not explicitly mentioned.",
            },
            {"concept": "Azure", "status": "gap", "surface_forms": ["Azure"], "evidence": ""},
        ],
        required_skills=["Cloud environment qualification (AWS, Azure)"],
        nice_to_have_skills=[],
        keywords=["AWS", "Azure"],
    )
    # CV text truthfully surfaces AWS but never claims Azure.
    text = "Anna Bauer qualified the company's first GxP cloud environment on AWS"
    report = _audit_cv_text(text, _CV, keywords=["AWS", "Azure"], ledger=ledger)
    assert "Azure" in report.keywords.missing
    assert "Azure" not in report.keywords.missing_claimable, (
        "a concept the ledger itself classifies 'gap' must never be presented as "
        "'supported by your profile'"
    )
    assert "Azure" in report.keywords.missing_honest_gap


def test_missing_keyword_unknown_to_ledger_is_honest_gap():
    """A missing keyword with no claimable ledger entry defaults to honest-gap —
    never silently claimable (mirrors the ledger's gap-default rule)."""
    report = _audit_cv_text("Anna Bauer", _CV, keywords=["Rust"], ledger=_LEDGER)
    assert report.keywords.missing == ["Rust"]
    assert report.keywords.missing_claimable == []
    assert report.keywords.missing_honest_gap == ["Rust"]


def test_no_ledger_all_missing_default_to_honest_gap():
    """Legacy pre-E037 path: no ledger → claimable bucket empty, all missing are honest-gap
    (back-compat — the panel still has something to show)."""
    report = _audit_cv_text("Anna Bauer", _CV, keywords=["Python", "GraphQL"])
    assert set(report.keywords.missing) == {"Python", "GraphQL"}
    assert report.keywords.missing_claimable == []
    assert set(report.keywords.missing_honest_gap) == {"Python", "GraphQL"}


def test_letter_missing_keywords_split_by_ledger():
    letter = {
        "header": {"name": "Anna Bauer", "email": None, "phone": None, "address": "Berlin"},
        "recipient": {"company": None, "name": None, "title": None, "address": None, "date": None},
        "body": {"paragraphs": ["Sehr geehrte Damen und Herren,"]},
        "signature": {"name": "Anna Bauer"},
    }
    text = "Anna Bauer Berlin Sehr geehrte Damen und Herren,"
    report = _audit_letter_text(text, letter, keywords=["Python", "GraphQL"], ledger=_LEDGER)
    assert report.keywords.missing_claimable == ["Python"]
    assert report.keywords.missing_honest_gap == ["GraphQL"]


def test_empty_letter_paragraph_skipped():
    letter = {
        "header": {"name": "Anna Bauer", "email": None, "phone": None, "address": "Berlin"},
        "recipient": {"company": None, "name": None, "title": None, "address": None, "date": None},
        "body": {"paragraphs": ["Sehr geehrte Damen und Herren,", "", "   "]},
        "signature": {"name": "Anna Bauer"},
    }
    text = "Anna Bauer Berlin Sehr geehrte Damen und Herren,"
    report = _audit_letter_text(text, letter, keywords=[])
    ids = {c.id for c in report.checks}
    assert "body-0" in ids
    assert "body-1" not in ids and "body-2" not in ids
    assert report.failed == 0
