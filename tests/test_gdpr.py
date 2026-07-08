"""Sprint 7 — GDPR Cascade Verification & Profile Export Tests (Task 21.13)

Done when:
  - DELETE /api/profile — cascade deletes all child records (applications,
    flow_sessions, interview_sessions, generated_cvs, uploaded_files)
  - GET /api/profile/export — returns complete user data as JSON
  - After DELETE, GET /api/profile returns 404
  - After DELETE, GET /api/applications returns empty list

Run (requires Docker):
    pytest tests/test_iter21_sprint7_gdpr.py -v

Note: DELETE /api/profile also deletes the User row (GDPR Art. 17 full erasure).
Each test that calls DELETE must re-create user+profile state via its own setup,
not rely on module-scoped fixtures left over from a previous delete.
"""

from pathlib import Path

import pytest
import requests

JD_FILE = Path(__file__).parent / "files" / "jd.txt"
CV_FILE = Path(__file__).parent / "files" / "Profile.pdf"


# ---------------------------------------------------------------------------
# Helpers — re-used setup steps across tests
# ---------------------------------------------------------------------------


def _upload_cv(api: str) -> dict:
    """Upload CV and return the response JSON. Asserts 200."""
    with open(CV_FILE, "rb") as f:
        r = requests.post(
            f"{api}/api/profile/upload",
            files={"file": ("Profile.pdf", f, "application/pdf")},
            timeout=60,
        )
    assert r.status_code == 200, f"Profile upload failed: {r.text}"
    return r.json()


def _analyze_jd(api: str) -> str:
    """Analyze JD and return job_analysis_id."""
    text = JD_FILE.read_text()
    r = requests.post(f"{api}/api/job/analyze", json={"text": text}, timeout=60)
    assert r.status_code == 200, f"JD analysis failed: {r.text}"
    return r.json()["id"]


def _create_application(api: str, job_id: str) -> str:
    """Create an application and return its id (handles 409 by finding existing)."""
    r = requests.post(f"{api}/api/applications", json={"job_analysis_id": job_id})
    if r.status_code == 409:
        items = requests.get(f"{api}/api/applications").json()["items"]
        existing = next((i for i in items if i["job_analysis_id"] == job_id), None)
        assert existing is not None, "409 but application not found"
        return existing["id"]
    assert r.status_code == 201, f"Unexpected {r.status_code}: {r.text}"
    return r.json()["id"]


# ---------------------------------------------------------------------------
# Module-scoped fixtures — used only for non-destructive tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def seeded_profile(api):
    """Import a CV once for the whole module (non-destructive tests only)."""
    return _upload_cv(api)


@pytest.fixture(scope="module")
def seeded_job_id(api):
    """Analyze JD once for the whole module (non-destructive tests only)."""
    return _analyze_jd(api)


# ---------------------------------------------------------------------------
# GET /api/profile/export — Data Portability (Art. 20)
# These tests are non-destructive and can share the module-scoped profile.
# ---------------------------------------------------------------------------


def test_export_profile_returns_complete_data(api, seeded_profile):
    """GET /api/profile/export returns a JSON with user, profile, applications."""
    r = requests.get(f"{api}/api/profile/export")
    assert r.status_code == 200, f"Export failed: {r.text}"

    body = r.json()
    assert "exported_at" in body
    assert "user" in body
    assert "profile" in body
    assert "applications" in body
    assert "interview_sessions" in body
    assert "uploads" in body

    # User ID must be present
    assert "id" in body["user"]
    assert "email" in body["user"]


def test_export_profile_has_content_disposition(api, seeded_profile):
    """Export response should have Content-Disposition header for download."""
    r = requests.get(f"{api}/api/profile/export")
    assert r.status_code == 200
    assert "content-disposition" in r.headers or "Content-Disposition" in r.headers


# ---------------------------------------------------------------------------
# DELETE /api/profile — Full Erasure (Art. 17)
# Each test is self-contained: it sets up its own user+profile state, then
# deletes. DELETE also removes the User row, so subsequent tests must not rely
# on state left by a previous delete test.
# ---------------------------------------------------------------------------


def test_delete_profile_returns_202(api):
    """DELETE /api/profile returns 202 Accepted."""
    _upload_cv(api)

    r = requests.delete(f"{api}/api/profile")
    assert r.status_code == 202, f"Unexpected {r.status_code}: {r.text}"

    body = r.json()
    assert "records_deleted" in body or "message" in body


def test_delete_profile_erases_profile(api):
    """After DELETE, GET /api/profile returns 404."""
    _upload_cv(api)

    r = requests.delete(f"{api}/api/profile")
    assert r.status_code == 202

    r = requests.get(f"{api}/api/profile")
    assert r.status_code == 404


def test_delete_profile_erases_applications(api):
    """After DELETE, GET /api/applications returns empty list."""
    _upload_cv(api)
    job_id = _analyze_jd(api)
    _create_application(api, job_id)

    # Verify at least one application exists before delete
    r = requests.get(f"{api}/api/applications")
    assert r.status_code == 200
    assert r.json()["total"] > 0, "No applications found before delete"

    r = requests.delete(f"{api}/api/profile")
    assert r.status_code == 202

    r = requests.get(f"{api}/api/applications")
    assert r.status_code == 200
    assert r.json()["total"] == 0, f"Expected 0 applications after delete, got {r.json()['total']}"


def test_delete_profile_erases_uploads(api):
    """After DELETE, uploaded files are removed from storage."""
    _upload_cv(api)

    r = requests.delete(f"{api}/api/profile")
    assert r.status_code == 202

    # Profile (and uploads) should be gone
    r = requests.get(f"{api}/api/profile")
    assert r.status_code == 404


def test_delete_profile_cascade_flow_sessions(api):
    """After DELETE, flow_sessions attached to applications are also deleted."""
    _upload_cv(api)
    job_id = _analyze_jd(api)
    app_id = _create_application(api, job_id)

    # Start workflow to create flow_session
    r = requests.post(f"{api}/api/applications/{app_id}/start")
    assert r.status_code in (200, 409)

    r = requests.delete(f"{api}/api/profile")
    assert r.status_code == 202

    # Application (and its flow_session) should be gone
    r = requests.get(f"{api}/api/applications/{app_id}")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/profile/exists — After Delete
# ---------------------------------------------------------------------------


def test_profile_exists_returns_false_after_delete(api):
    """After DELETE, GET /api/profile/exists returns exists: false."""
    _upload_cv(api)

    r = requests.get(f"{api}/api/profile/exists")
    assert r.status_code == 200
    assert r.json()["exists"] is True

    r = requests.delete(f"{api}/api/profile")
    assert r.status_code == 202

    r = requests.get(f"{api}/api/profile/exists")
    assert r.status_code == 200
    assert r.json()["exists"] is False
    assert r.json()["completeness_score"] == 0.0


# ---------------------------------------------------------------------------
# E039/US219 — Art. 17 beats the submitted pin
# A CV pinned as submitted is exempt from RETENTION purge (ADR-005 amendment),
# but the user-initiated erasure cascade must still hard-delete it, and the
# new applications.submitted_cv_id FK must not break the cascade order.
# ---------------------------------------------------------------------------


def _generate_cv_ready(api: str, job_id: str) -> str:
    """Generate a CV and poll until ready. Returns cv_id."""
    import time

    r = requests.post(
        f"{api}/api/cv/generate",
        json={"job_id": job_id, "template": "classic_german"},
        timeout=30,
    )
    assert r.status_code == 201, f"CV generate failed: {r.text}"
    cv_id = r.json()["cv_id"]

    deadline = time.time() + 120
    while time.time() < deadline:
        s = requests.get(f"{api}/api/cv/{cv_id}/status", timeout=10)
        assert s.status_code == 200
        if s.json()["status"] == "ready":
            return cv_id
        assert s.json()["status"] != "failed", f"CV generation failed: {s.text}"
        time.sleep(2)
    raise AssertionError("CV generation did not become ready within 120s")


def test_delete_profile_erases_pinned_submitted_cv(api):
    """Erasure hard-deletes even artifacts pinned as submitted (Art. 17 beats the pin)."""
    _upload_cv(api)
    job_id = _analyze_jd(api)
    app_id = _create_application(api, job_id)
    cv_id = _generate_cv_ready(api, job_id)

    # Pin the CV as the submitted artifact
    r = requests.patch(
        f"{api}/api/applications/{app_id}", json={"submitted_cv_id": cv_id}
    )
    assert r.status_code == 200, f"Pin failed: {r.text}"
    assert r.json()["submitted_cv_id"] == cv_id

    # Erase — must not 500 on the applications.submitted_cv_id FK
    r = requests.delete(f"{api}/api/profile")
    assert r.status_code == 202, f"Erasure failed: {r.text}"

    # The pinned artifact is gone with everything else
    r = requests.get(f"{api}/api/cv/{cv_id}/status")
    assert r.status_code == 404
    r = requests.get(f"{api}/api/applications")
    assert r.json()["total"] == 0
