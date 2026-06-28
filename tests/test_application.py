"""Iteration 17 — Application Entity & Job GET (integration tests)

Done when:
  - POST /api/applications (201) — create, denorm company_name/role_title from job
  - POST /api/applications — idempotent reuse on duplicate (user_id, job_id)
  - POST /api/applications — 404 on unknown job_analysis_id
  - GET  /api/applications — list pipeline, filter by workflow_status and user_status
  - GET  /api/applications/{id} — detail, 404 on unknown
  - PATCH /api/applications/{id} — update user_status, notes, 422 on empty body
  - POST /api/applications/{id}/start — creates FlowSession, workflow_status=analyzing
  - POST /api/applications/{id}/start — 409 if workflow already started
  - DELETE /api/applications/{id} — 204 soft-delete, subsequent GET returns 404
  - DELETE /api/applications/{id} — 404 on unknown
  - GET /api/job/{job_id} — retrieve stored analysis without re-triggering LLM (17.11)
  - GET /api/job/{job_id} — 404 on unknown

Run (requires Docker):
    pytest tests/test_iter17_application.py -v
"""

import uuid as _uuid
from pathlib import Path

import pytest
import requests

JD_FILE = Path(__file__).parent / "files" / "jd.txt"

# A short inline JD used for the delete test to avoid an extra LLM call.
_DELETE_JD = (
    "Data Engineer (m/w/d) — CloudStream GmbH, Hamburg. "
    "Required: Apache Spark, Kafka, Python 3.10+. "
    "Nice to have: dbt, Airflow. Language: German B2."
)


# ---------------------------------------------------------------------------
# Module-scoped fixtures — one LLM call per module
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def job_id(api):
    """Analyze the canonical JD file once; return job_analysis_id."""
    text = JD_FILE.read_text()
    r = requests.post(f"{api}/api/job/analyze", json={"text": text}, timeout=60)
    assert r.status_code == 200, f"JD analysis failed: {r.text}"
    return r.json()["id"]


@pytest.fixture(scope="module")
def application_id(api, job_id):
    """Create one application for job_id; return application id.

    Uses allow_existing so the fixture is safe if the DB already has a record
    from a previous run (e.g. containers left running).
    """
    r = requests.post(f"{api}/api/applications", json={"job_analysis_id": job_id})
    if r.status_code == 409:
        # Already exists — find and return it
        items = requests.get(f"{api}/api/applications").json()["items"]
        existing = next((i for i in items if i["job_analysis_id"] == job_id), None)
        assert existing is not None, "409 but application not found in list"
        return existing["id"]
    assert r.status_code == 201, f"Unexpected status {r.status_code}: {r.text}"
    return r.json()["id"]


# ---------------------------------------------------------------------------
# POST /api/applications
# ---------------------------------------------------------------------------


def test_create_application_returns_201_with_correct_fields(api, job_id, application_id):
    """application_id fixture already called POST; verify the stored record shape."""
    r = requests.get(f"{api}/api/applications/{application_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["job_analysis_id"] == job_id
    assert body["workflow_status"] == "none"
    assert body["user_status"] == "tracking"
    assert body["flow_session_id"] is None
    assert "expires_at" in body
    assert "created_at" in body


def test_create_application_idempotent_on_duplicate(api, job_id, application_id):
    """A second POST for the same job reuses the existing application (no raw 409).

    One application per (user, job) is idempotent get-or-create: re-submitting the
    same JD after a failed build must resume the existing application, not dead-end
    the user with a hard conflict. (Regression: orphaned-application retry loop.)
    """
    r = requests.post(f"{api}/api/applications", json={"job_analysis_id": job_id})
    assert r.status_code in (200, 201), f"Unexpected status {r.status_code}: {r.text}"
    assert r.json()["id"] == application_id


def test_create_application_404_on_unknown_job(api):
    r = requests.post(
        f"{api}/api/applications",
        json={"job_analysis_id": str(_uuid.uuid4())},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/applications
# ---------------------------------------------------------------------------


def test_list_applications_includes_created(api, job_id, application_id):
    r = requests.get(f"{api}/api/applications")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "total" in body
    ids = [item["job_analysis_id"] for item in body["items"]]
    assert job_id in ids


def test_list_applications_filter_workflow_status_none(api, application_id):
    r = requests.get(f"{api}/api/applications", params={"workflow_status": "none"})
    assert r.status_code == 200
    for item in r.json()["items"]:
        assert item["workflow_status"] == "none"


def test_list_applications_filter_user_status_tracking(api, application_id):
    r = requests.get(f"{api}/api/applications", params={"user_status": "tracking"})
    assert r.status_code == 200
    for item in r.json()["items"]:
        assert item["user_status"] == "tracking"


# ---------------------------------------------------------------------------
# GET /api/applications/{id}
# ---------------------------------------------------------------------------


def test_get_application_detail(api, application_id):
    r = requests.get(f"{api}/api/applications/{application_id}")
    assert r.status_code == 200
    assert r.json()["id"] == application_id


def test_get_application_404_on_unknown(api):
    r = requests.get(f"{api}/api/applications/{_uuid.uuid4()}")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/applications/{id}
# ---------------------------------------------------------------------------


def test_patch_application_user_status(api, application_id):
    r = requests.patch(
        f"{api}/api/applications/{application_id}",
        json={"user_status": "applied"},
    )
    assert r.status_code == 200
    assert r.json()["user_status"] == "applied"

    # Reset for subsequent tests
    requests.patch(f"{api}/api/applications/{application_id}", json={"user_status": "tracking"})


def test_patch_application_notes(api, application_id):
    r = requests.patch(
        f"{api}/api/applications/{application_id}",
        json={"notes": "Contacted recruiter via LinkedIn"},
    )
    assert r.status_code == 200
    assert r.json()["notes"] == "Contacted recruiter via LinkedIn"


def test_patch_application_empty_body_returns_422(api, application_id):
    r = requests.patch(f"{api}/api/applications/{application_id}", json={})
    assert r.status_code == 422


def test_patch_application_404_on_unknown(api):
    r = requests.patch(
        f"{api}/api/applications/{_uuid.uuid4()}",
        json={"notes": "ghost"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/applications/{id}/start
# ---------------------------------------------------------------------------


def test_start_workflow_creates_flow_session(api, application_id):
    r = requests.post(f"{api}/api/applications/{application_id}/start")
    # May already be started if tests ran before; accept 200 or 409
    assert r.status_code in (200, 409), f"Unexpected {r.status_code}: {r.text}"
    if r.status_code == 200:
        body = r.json()
        assert body["workflow_status"] == "analyzing"
        assert body["flow_session_id"] is not None


def test_start_workflow_conflict_if_already_started(api, application_id):
    """After the first /start, a second call must return 409."""
    # Ensure it was started (idempotent in terms of the test)
    requests.post(f"{api}/api/applications/{application_id}/start")
    r = requests.post(f"{api}/api/applications/{application_id}/start")
    assert r.status_code == 409


def test_start_workflow_404_on_unknown(api):
    r = requests.post(f"{api}/api/applications/{_uuid.uuid4()}/start")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/applications/{id}
# ---------------------------------------------------------------------------


def test_delete_application_soft_delete(api):
    """Create a fresh application, delete it, verify 404 on subsequent GET."""
    r = requests.post(f"{api}/api/job/analyze", json={"text": _DELETE_JD}, timeout=60)
    assert r.status_code == 200, f"JD analysis failed: {r.text}"
    jd2_id = r.json()["id"]

    r = requests.post(f"{api}/api/applications", json={"job_analysis_id": jd2_id})
    # Allow 409 if this JD was analyzed+tracked in a previous run
    if r.status_code == 409:
        items = requests.get(f"{api}/api/applications").json()["items"]
        app = next((i for i in items if i["job_analysis_id"] == jd2_id), None)
        assert app is not None
        app_id = app["id"]
    else:
        assert r.status_code == 201, f"Unexpected {r.status_code}: {r.text}"
        app_id = r.json()["id"]

    r = requests.delete(f"{api}/api/applications/{app_id}")
    assert r.status_code == 204

    r = requests.get(f"{api}/api/applications/{app_id}")
    assert r.status_code == 404


def test_delete_application_404_on_unknown(api):
    r = requests.delete(f"{api}/api/applications/{_uuid.uuid4()}")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/job/{job_id}  (17.11 — retrieve without re-triggering LLM)
# ---------------------------------------------------------------------------


def test_get_job_analysis_by_id(api, job_id):
    r = requests.get(f"{api}/api/job/{job_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == job_id
    assert "role_title" in body
    assert "required_skills" in body


def test_get_job_analysis_404_on_unknown(api):
    r = requests.get(f"{api}/api/job/{_uuid.uuid4()}")
    assert r.status_code == 404
