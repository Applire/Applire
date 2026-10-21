"""
Iteration 12 — CV Upload & Parsing Pipeline (integration tests)

Done when:
  - POST /api/profile/import-jobs (async, then poll) with a real DACH CV PDF
    → completeness_score > 0.6, all major sections populated.
  - Upload a second CV → conflicts are flagged, no data lost
    (GET /api/profile work_experience is non-empty).
  - Import of an empty/invalid file → a failed job with a stable error_code.
  - The retired sync doors POST /api/profile/upload and /api/profile/import
    → 410 Gone with a pointer to /api/profile/import-jobs (ruling V-4 = A).
  - LLM timeout → 504.

Run (Docker required):
    python -m pytest tests/test_iter12_cv_upload.py -v

Fixture files:
    tests/files/Profile.pdf — LinkedIn PDF export (already present from iter 9)
"""

from pathlib import Path

import pytest
import requests

_BASE = "http://localhost:8001"
_JOBS_URL = f"{_BASE}/api/profile/import-jobs"
_RETIRED_UPLOAD_URL = f"{_BASE}/api/profile/upload"
_RETIRED_IMPORT_URL = f"{_BASE}/api/profile/import"
_PROFILE_URL = f"{_BASE}/api/profile"

_PDF_FILE = Path(__file__).parent / "files" / "Profile.pdf"
_JD_FILE = Path(__file__).parent / "files" / "jd.txt"


# ---------------------------------------------------------------------------
# 12.1 — Upload a DACH PDF → completeness > 0.6
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_upload_pdf_returns_cvupload_response(import_job):
    assert _PDF_FILE.exists(), f"Fixture missing: {_PDF_FILE}"
    body = import_job.result(_BASE, "Profile.pdf", _PDF_FILE.read_bytes(), "application/pdf")
    assert "profile_id" in body
    assert "completeness_score" in body
    assert "status" in body
    assert body["status"] in ("DRAFT", "COMPLETE")
    assert "expires_at" in body
    assert "enrichment_record_id" in body


@pytest.mark.integration
def test_upload_pdf_completeness_above_threshold(import_job):
    assert _PDF_FILE.exists()
    body = import_job.result(_BASE, "Profile.pdf", _PDF_FILE.read_bytes(), "application/pdf")
    assert body["completeness_score"] > 0.6, (
        f"Expected completeness > 0.6, got {body['completeness_score']}"
    )


@pytest.mark.integration
def test_upload_pdf_populates_profile(import_job):
    import_job.result(_BASE, "Profile.pdf", _PDF_FILE.read_bytes(), "application/pdf")
    profile_resp = requests.get(_PROFILE_URL, timeout=30)
    assert profile_resp.status_code == 200
    profile = profile_resp.json()["profile"]
    assert len(profile.get("work_experience", [])) > 0
    assert profile.get("personal_info", {}).get("name", "") != ""


# ---------------------------------------------------------------------------
# 12.2 — Second import triggers merge, no data lost
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_second_upload_does_not_lose_work_experience(import_job):
    # Import twice (same file — idempotent merge); a failed job is tolerated here,
    # the assertion is on the profile that survives.
    for _ in range(2):
        import_job.run(_BASE, "Profile.pdf", _PDF_FILE.read_bytes(), "application/pdf")

    profile_resp = requests.get(_PROFILE_URL, timeout=30)
    assert profile_resp.status_code == 200
    assert len(profile_resp.json()["profile"].get("work_experience", [])) > 0


# ---------------------------------------------------------------------------
# 12.3 — Import with optional job_id parameter
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_upload_with_job_id_query_param(import_job):
    """Passing a non-existent job_id is accepted (202) and the job still settles ready."""
    import time

    fake_job_id = "00000000-0000-0000-0000-000000000000"
    resp = requests.post(
        _JOBS_URL,
        params={"job_id": fake_job_id},
        files={"file": ("Profile.pdf", _PDF_FILE.read_bytes(), "application/pdf")},
        timeout=60,
    )
    # Non-existent job_id should not fail — falls back to generic extraction
    assert resp.status_code == 202, resp.text
    import_id = resp.json()["import_id"]
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        poll = requests.get(f"{_JOBS_URL}/{import_id}", timeout=30)
        assert poll.status_code == 200
        if poll.json()["status"] in ("ready", "failed"):
            break
        time.sleep(2)
    assert poll.json()["status"] == "ready", poll.text


# ---------------------------------------------------------------------------
# 12.4 — Empty / invalid file → a failed job with a stable error_code
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_upload_empty_file_fails_the_job(import_job):
    """The async door accepts the submit (202); the job settles ``failed`` with a code."""
    poll = import_job.run(_BASE, "empty.pdf", b"", "application/pdf")
    body = poll.json()
    assert body["status"] == "failed", body
    assert body["error_code"] in ("invalid_document", "import_failed"), body


# ---------------------------------------------------------------------------
# 12.5 — The retired sync doors answer 410 Gone with a pointer to import-jobs
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("url", [_RETIRED_UPLOAD_URL, _RETIRED_IMPORT_URL])
def test_retired_sync_import_doors_answer_410(url):
    """POST /api/profile/upload and /import were retired (V-4 = A): 410 + the job door."""
    resp = requests.post(
        url,
        files={"file": ("cv.txt", b"plain text content", "text/plain")},
        timeout=30,
    )
    assert resp.status_code == 410
    assert resp.json()["use"] == "/api/profile/import-jobs"
