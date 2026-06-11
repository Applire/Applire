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
