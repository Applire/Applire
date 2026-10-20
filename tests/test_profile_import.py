"""
Iteration 2 — Profile Import
Done when: Upload a real CV PDF → GET /api/profile returns a structured
           MasterProfile JSON with all major sections populated.
"""
import io
import json
from pathlib import Path

import pytest
import requests

_REAL_CV_PDF = Path(__file__).parent / "files" / "Profile.pdf"

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_VALID_SECTIONS = {"work_experience", "skills", "education", "languages", "personal_info"}


def _make_cv_pdf() -> bytes:
    """Build a minimal valid PDF (PDF 1.4) with embedded German-format CV text."""
    lines = [
        "Max Mustermann - Senior Software Engineer",
        "Email: max.mustermann@example.de | +49 89 12345678",
        "Munich, Germany | linkedin.com/in/max-mustermann",
        "BERUFSERFAHRUNG / WORK EXPERIENCE",
        "Senior Software Engineer, Acme GmbH Munich, 2021-01 bis heute",
        "  - FastAPI/Python Microservices fuer 1 Mio. Anfragen/Tag",
        "  - Migration Monolith zu Microservices; Latenz um 40% reduziert",
        "  - Fachliche Fuehrung von 3 Juniorentwicklern",
        "Software Engineer, Startup AG Berlin, 2018-06 bis 2020-12",
        "  - REST-APIs und ETL-Datenpipelines in Python entwickelt",
        "  - CI/CD-Pipelines mit GitHub Actions und Docker aufgebaut",
        "AUSBILDUNG / EDUCATION",
        "Technische Universitaet Muenchen (TUM)",
        "Bachelor of Science Informatik, 2014-2018",
        "KENNTNISSE / SKILLS",
        "Python, FastAPI, PostgreSQL, SQLAlchemy, Docker, Kubernetes, Git, Redis",
        "SPRACHEN / LANGUAGES",
        "Deutsch: Muttersprache | Englisch: C1 (fließend)",
    ]

    ops = []
    y = 740
    for line in lines:
        safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append(f"BT /F1 11 Tf 40 {y} Td ({safe}) Tj ET")
        y -= 16

    stream = "\n".join(ops).encode("latin-1")
    stream_len = len(stream)

    obj1 = b"<< /Type /Catalog /Pages 2 0 R >>"
    obj2 = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
    obj3 = (
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
        b" /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
    )
    obj4 = f"<< /Length {stream_len} >>\nstream\n".encode() + stream + b"\nendstream"
    obj5 = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    objects = [obj1, obj2, obj3, obj4, obj5]

    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")

    offsets = []
    for i, obj_data in enumerate(objects, start=1):
        offsets.append(buf.tell())
        buf.write(f"{i} 0 obj\n".encode())
        buf.write(obj_data)
        buf.write(b"\nendobj\n")

    xref_offset = buf.tell()
    buf.write(b"xref\n")
    buf.write(f"0 {len(objects) + 1}\n".encode())
    buf.write(b"0000000000 65535 f \n")
    for offset in offsets:
        buf.write(f"{offset:010d} 00000 n \n".encode())

    buf.write(b"trailer\n")
    buf.write(f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode())
    buf.write(b"startxref\n")
    buf.write(f"{xref_offset}\n".encode())
    buf.write(b"%%EOF\n")

    return buf.getvalue()


_CV_PDF = _make_cv_pdf()

_LINKEDIN_JSON = {
    "firstName": "Anna",
    "lastName": "Beispiel",
    "emailAddress": "anna.beispiel@example.de",
    "headline": "Product Manager | SaaS | DACH",
    "location": {"name": "Hamburg, Germany"},
    "positions": [
        {
            "title": "Senior Product Manager",
            "companyName": "Munich Tech GmbH",
            "startDate": {"month": 3, "year": 2020},
            "endDate": None,
            "description": "Led B2B SaaS product roadmap; grew ARR by 60% YoY.",
        },
        {
            "title": "Business Analyst",
            "companyName": "Consulting AG",
            "startDate": {"month": 6, "year": 2017},
            "endDate": {"month": 2, "year": 2020},
            "description": "Requirements engineering and stakeholder management for automotive clients.",
        },
    ],
    "educations": [
        {
            "schoolName": "Ludwig-Maximilians-Universitaet Muenchen",
            "degreeName": "Master of Science",
            "fieldOfStudy": "Wirtschaftsinformatik",
            "startDate": {"year": 2015},
            "endDate": {"year": 2017},
        }
    ],
    "skills": [
        {"name": "Product Management"},
        {"name": "Agile / Scrum"},
        {"name": "SQL"},
        {"name": "Jira"},
        {"name": "OKR"},
    ],
    "languages": [
        {"language": {"name": "German"}, "proficiency": "NATIVE_OR_BILINGUAL"},
        {"language": {"name": "English"}, "proficiency": "PROFESSIONAL_WORKING"},
    ],
}


def _ready(poll: requests.Response) -> dict:
    """The CVUploadResponse of a settled import job; fails the test if the job failed."""
    body = poll.json()
    assert body["status"] == "ready", f"import job failed: {body.get('error_code')} {body}"
    return body["result"]


def _import_pdf(api: str, import_job) -> requests.Response:
    """Import the synthetic CV PDF through the async job; returns the final poll."""
    return import_job.run(api, "cv.pdf", _CV_PDF, "application/pdf")


def _import_linkedin(api: str, import_job) -> requests.Response:
    """Import the LinkedIn JSON export (a .json file, ?source=linkedin); returns the final poll."""
    return import_job.run(
        api,
        "linkedin-export.json",
        json.dumps(_LINKEDIN_JSON).encode("utf-8"),
        "application/json",
        source="linkedin",
    )


def _get_profile(api: str) -> requests.Response:
    return requests.get(f"{api}/api/profile", timeout=10)


# ---------------------------------------------------------------------------
# PDF import tests
# ---------------------------------------------------------------------------


def test_import_pdf_returns_200(api, import_job):
    r = _import_pdf(api, import_job)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ready", r.text


def test_import_pdf_response_structure(api, import_job):
    body = _ready(_import_pdf(api, import_job))

    assert isinstance(body.get("profile_id"), str) and len(body["profile_id"]) == 36
    assert isinstance(body.get("completeness_score"), float)
    assert 0.0 <= body["completeness_score"] <= 1.0
    assert isinstance(body.get("status"), str)
    assert body["status"] in ("DRAFT", "COMPLETE")
    assert isinstance(body.get("enrichment_record_id"), str)


def test_import_pdf_extracts_major_sections(api, import_job):
    _import_pdf(api, import_job)
    profile = requests.get(f"{api}/api/profile", timeout=10).json()["profile"]

    assert profile["work_experience"], "Expected at least one work entry"
    entry = profile["work_experience"][0]
    assert isinstance(entry.get("company"), str) and entry["company"]
    assert isinstance(entry.get("role"), str) and entry["role"]
    assert isinstance(entry.get("start_date"), str) and entry["start_date"]

    assert profile["skills"], "Expected at least one skill"
    assert all(isinstance(s, dict) and "name" in s for s in profile["skills"])

    assert profile["education"], "Expected at least one education entry"
    assert profile["languages"], "Expected at least one language"

    personal_info = profile["personal_info"]
    assert isinstance(personal_info, dict)


# ---------------------------------------------------------------------------
# LinkedIn import tests
# ---------------------------------------------------------------------------


def test_import_linkedin_returns_200(api, import_job):
    r = _import_linkedin(api, import_job)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ready", r.text


def test_import_linkedin_response_structure(api, import_job):
    body = _ready(_import_linkedin(api, import_job))
    assert isinstance(body.get("profile_id"), str) and len(body["profile_id"]) == 36
    # The job result is a CVUploadResponse (not the profile): read the profile itself.
    profile = _get_profile(api).json()["profile"]
    assert isinstance(profile.get("work_experience"), list)
    assert isinstance(profile.get("skills"), list)
    assert isinstance(profile.get("education"), list)
    assert isinstance(profile.get("languages"), list)
    assert isinstance(profile.get("personal_info"), dict)


def test_import_linkedin_extracts_positions(api, import_job):
    _ready(_import_linkedin(api, import_job))
    profile = _get_profile(api).json()["profile"]

    assert len(profile["work_experience"]) >= 2, "Expected both LinkedIn positions"
    for entry in profile["work_experience"]:
        assert isinstance(entry.get("company"), str) and entry["company"]
        assert isinstance(entry.get("role"), str) and entry["role"]


# ---------------------------------------------------------------------------
# GET /api/profile tests (depends on profile having been imported above)
# ---------------------------------------------------------------------------


def test_get_profile_returns_200(api, import_job):
    _import_pdf(api, import_job)  # ensure a profile exists
    r = _get_profile(api)
    assert r.status_code == 200, r.text


def test_get_profile_structure(api, import_job):
    _import_pdf(api, import_job)
    body = _get_profile(api).json()

    assert isinstance(body.get("id"), str)
    assert isinstance(body.get("profile"), dict)
    assert isinstance(body.get("completeness"), float)
    assert 0.0 <= body["completeness"] <= 1.0


def test_get_profile_completeness_reflects_content(api, import_job):
    _import_pdf(api, import_job)
    body = _get_profile(api).json()
    # A CV with work experience, skills, education, languages and personal_info
    # should score at least 0.6 (3 of 5 sections).
    assert body["completeness"] >= 0.6


# ---------------------------------------------------------------------------
# PATCH /api/profile/{section} tests
# ---------------------------------------------------------------------------


def test_patch_skills_returns_200(api, import_job):
    _import_pdf(api, import_job)
    r = requests.patch(
        f"{api}/api/profile/skills",
        json=["Python", "FastAPI", "PostgreSQL", "Patched via test"],
        timeout=10,
    )
    assert r.status_code == 200, r.text


def test_patch_skills_updates_value(api, import_job):
    _import_pdf(api, import_job)
    new_skills = ["TestSkill-Alpha", "TestSkill-Beta"]
    r = requests.patch(
        f"{api}/api/profile/skills",
        json=new_skills,
        timeout=10,
    )
    assert r.status_code == 200, r.text
    updated = r.json()["profile"]["skills"]
    # Skills are now Skill objects; compare by name
    updated_names = [s["name"] for s in updated]
    assert updated_names == new_skills


def test_patch_contact_returns_200(api, import_job):
    _import_pdf(api, import_job)
    r = requests.patch(
        f"{api}/api/profile/personal_info",
        json={"name": "Anna Bauer", "email": "patched@example.de", "location": "Berlin"},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    personal_info = r.json()["profile"]["personal_info"]
    assert personal_info["email"] == "patched@example.de"


def test_patch_work_history_returns_200(api, import_job):
    _import_pdf(api, import_job)
    new_entry = {
        "company": "Test Corp",
        "role": "Test Engineer",
        "start_date": "2023-01",
        "end_date": None,
        "responsibilities": ["Wrote tests", "Fixed bugs"],
    }
    r = requests.patch(
        f"{api}/api/profile/work_experience",
        json=[new_entry],
        timeout=10,
    )
    assert r.status_code == 200, r.text
    history = r.json()["profile"]["work_experience"]
    assert len(history) == 1
    assert history[0]["company"] == "Test Corp"


# ---------------------------------------------------------------------------
# Error / validation tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/profile/import", "/api/profile/upload"])
def test_retired_sync_import_doors_answer_410(api, path):
    """The sync doors were retired (V-4 = A): 410 Gone, pointing at the job door."""
    r = requests.post(f"{api}{path}", timeout=10)
    assert r.status_code == 410
    assert r.json()["use"] == "/api/profile/import-jobs"


def test_import_job_rejects_no_input(api):
    r = requests.post(f"{api}/api/profile/import-jobs", timeout=10)
    assert r.status_code == 422


def test_import_job_rejects_non_pdf_file(api, import_job):
    """A plain-text file is not a CV: the job settles ``failed`` with a stable code."""
    body = import_job.run(api, "cv.txt", b"not a pdf", "text/plain").json()
    assert body["status"] == "failed", body
    assert body["error_code"], body


def test_import_job_rejects_invalid_linkedin_json(api, import_job):
    """A LinkedIn JSON export that is not JSON settles ``failed`` (invalid_document)."""
    body = import_job.run(
        api, "linkedin-export.json", b"this is not json {{{", "application/json", source="linkedin"
    ).json()
    assert body["status"] == "failed", body
    assert body["error_code"] == "invalid_document", body


def test_patch_invalid_section_returns_422(api):
    r = requests.patch(
        f"{api}/api/profile/nonexistent_section",
        json=["anything"],
        timeout=10,
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Real LinkedIn PDF (tests/files/Profile.pdf)
# ---------------------------------------------------------------------------


def _import_real_pdf(api: str, import_job) -> requests.Response:
    pdf_bytes = _REAL_CV_PDF.read_bytes()
    return import_job.run(api, "Profile.pdf", pdf_bytes, "application/pdf")


def test_real_pdf_import_returns_200(api, import_job):
    r = _import_real_pdf(api, import_job)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ready", r.text


def test_real_pdf_response_structure(api, import_job):
    body = _ready(_import_real_pdf(api, import_job))

    assert isinstance(body.get("profile_id"), str) and len(body["profile_id"]) == 36
    assert isinstance(body.get("completeness_score"), float)
    assert 0.0 <= body["completeness_score"] <= 1.0
    assert isinstance(body.get("status"), str)
    assert body["status"] in ("DRAFT", "COMPLETE")


def test_real_pdf_extracts_work_history(api, import_job):
    _import_real_pdf(api, import_job)
    profile = requests.get(f"{api}/api/profile", timeout=10).json()["profile"]

    assert profile["work_experience"], "Expected at least one work entry from real CV"
    entry = profile["work_experience"][0]
    assert isinstance(entry.get("company"), str) and entry["company"]
    assert isinstance(entry.get("role"), str) and entry["role"]
    assert isinstance(entry.get("start_date"), str) and entry["start_date"]


def test_real_pdf_extracts_skills(api, import_job):
    _import_real_pdf(api, import_job)
    profile = requests.get(f"{api}/api/profile", timeout=10).json()["profile"]
    assert profile["skills"], "Expected skills extracted from real CV"
    assert all(isinstance(s, dict) and "name" in s for s in profile["skills"])


def test_real_pdf_completeness_high(api, import_job):
    body = _import_real_pdf(api, import_job).json()
    # A complete LinkedIn PDF should populate most sections → ≥ 0.70
    assert body["completeness_score"] >= 0.70, (
        f"Completeness {body['completeness_score']} is lower than expected for a full LinkedIn profile"
    )
