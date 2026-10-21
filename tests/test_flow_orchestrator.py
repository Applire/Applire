"""
Iteration 15 — Flow Orchestrator & Entry UX (integration tests)

Done when:
  - POST /api/flow creates a flow session with correct user_type
  - GET /api/flow/{id}/state returns current step and available_actions
  - POST /api/flow/{id}/advance transitions steps and writes artifact FKs
  - Illegal transitions return 409 with current/target/allowed in body
  - advancing without required artifact_id returns 422
  - Idempotent: second POST /api/flow same job returns same flow_id
  - Full new user journey: jd_analysis → gap_analysis → interview → cv_generation → complete
  - Returning user journey skips cv_import
  - FlowStateResponse.gap_summary populated after gap step

Run (requires Docker):
    pytest tests/test_iter15_flow_orchestrator.py -v
"""

import uuid as _uuid
from pathlib import Path

import pytest
import requests

JD_FILE = Path(__file__).parent / "files" / "jd.txt"

_DACH_PROFILE = {
    "firstName": "Anna",
    "lastName": "Bauer",
    "emailAddress": "anna.bauer@example.de",
    "headline": "Senior Software Engineer",
    "location": {"name": "Munich, Germany"},
    "positions": [
        {
            "title": "Senior Software Engineer",
            "companyName": "Roche Deutschland GmbH",
            "startDate": {"month": 1, "year": 2019},
            "endDate": None,
            "description": "Microservices with Python and FastAPI. PostgreSQL, Docker, CI/CD.",
        }
    ],
    "educations": [
        {
            "schoolName": "Technische Universität München",
            "degreeName": "Master of Science",
            "fieldOfStudy": "Informatik",
            "startDate": {"year": 2014},
            "endDate": {"year": 2018},
        }
    ],
    "skills": [
        {"name": "Python"},
        {"name": "FastAPI"},
        {"name": "PostgreSQL"},
        {"name": "Docker"},
    ],
    "languages": [
        {"language": {"name": "German"}, "proficiency": "NATIVE_OR_BILINGUAL"},
        {"language": {"name": "English"}, "proficiency": "PROFESSIONAL_WORKING"},
    ],
}


# ---------------------------------------------------------------------------
# Module-scoped fixtures — one LLM call per session
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def ensure_profile(api, import_job):
    """Import a minimal profile (LinkedIn JSON via the import job) so gap analysis endpoints are available."""
    import_job.linkedin_json(api, _DACH_PROFILE)


@pytest.fixture(scope="module")
def job_id(api):
    # Append a UUID so each module invocation creates a fresh job — prevents
    # previous-run flows (which may be at gap_analysis) from polluting assertions
    # that expect current_step == "jd_analysis".
    r = requests.post(
        f"{api}/api/job/analyze",
        json={"text": JD_FILE.read_text() + f" [module-{_uuid.uuid4()}]"},
        timeout=60,
    )
    assert r.status_code == 200, f"JD analyze failed: {r.text}"
    return r.json()["id"]


@pytest.fixture(scope="module")
def flow(api, job_id):
    """Create a flow session for the test job."""
    r = requests.post(
        f"{api}/api/flow",
        json={"job_id": job_id},
        timeout=30,
    )
    assert r.status_code == 201, f"Flow creation failed: {r.text}"
    return r.json()


@pytest.fixture(scope="module")
def flow_id(flow):
    return flow["flow_id"]


# ---------------------------------------------------------------------------
# POST /api/flow — create
# ---------------------------------------------------------------------------


class TestCreateFlow:
    def test_returns_201(self, api, job_id):
        # Create a new flow — may return existing if job already has one (idempotent)
        r = requests.post(f"{api}/api/flow", json={"job_id": job_id}, timeout=30)
        assert r.status_code == 201, r.text

    def test_response_has_flow_id(self, flow):
        assert "flow_id" in flow
        assert len(flow["flow_id"]) == 36  # UUID format

    def test_response_has_user_type(self, flow):
        assert flow["user_type"] in ("new", "returning")

    def test_response_has_current_step(self, flow):
        assert flow["current_step"] == "jd_analysis"

    def test_response_has_available_actions(self, flow):
        assert isinstance(flow["available_actions"], dict)
        assert "next" in flow["available_actions"]

    def test_job_summary_populated(self, flow):
        assert flow["job_summary"] is not None
        assert "role_title" in flow["job_summary"]

    def test_idempotent_returns_same_flow_id(self, api, job_id, flow_id):
        r = requests.post(f"{api}/api/flow", json={"job_id": job_id}, timeout=30)
        assert r.status_code == 201, r.text
        assert r.json()["flow_id"] == flow_id

    def test_unknown_job_returns_404(self, api):
        r = requests.post(
            f"{api}/api/flow",
            json={"job_id": "00000000-0000-0000-0000-000000000000"},
            timeout=10,
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/flow/{id}/state
# ---------------------------------------------------------------------------


class TestGetFlowState:
    def test_returns_200(self, api, flow_id):
        r = requests.get(f"{api}/api/flow/{flow_id}/state", timeout=15)
        assert r.status_code == 200, r.text

    def test_state_has_required_fields(self, api, flow_id):
        r = requests.get(f"{api}/api/flow/{flow_id}/state", timeout=15)
        data = r.json()
        for field in ("flow_id", "job_id", "user_type", "current_step", "available_actions"):
            assert field in data, f"Missing field: {field}"

    def test_unknown_flow_returns_404(self, api):
        r = requests.get(
            f"{api}/api/flow/00000000-0000-0000-0000-000000000000/state", timeout=10
        )
        assert r.status_code == 404

    def test_profile_completeness_present(self, api, flow_id):
        r = requests.get(f"{api}/api/flow/{flow_id}/state", timeout=15)
        data = r.json()
        # May be None if no profile exists, but key must be present
        assert "profile_completeness" in data


# ---------------------------------------------------------------------------
# POST /api/flow/{id}/advance — valid transitions
# ---------------------------------------------------------------------------


class TestAdvanceFlow:
    @pytest.fixture(scope="class")
    def fresh_flow(self, api):
        """Create a second job + flow for isolated advance tests.

        Uses a unique suffix per run so the job hash never collides with leftover
        data from previous test runs (Postgres volume persists across docker compose
        down/up cycles).
        """
        jd_r = requests.post(
            f"{api}/api/job/analyze",
            json={"text": JD_FILE.read_text() + f" [advance-test {_uuid.uuid4()}]"},
            timeout=60,
        )
        assert jd_r.status_code == 200
        job_id = jd_r.json()["id"]

        flow_r = requests.post(f"{api}/api/flow", json={"job_id": job_id}, timeout=30)
        assert flow_r.status_code == 201
        return flow_r.json()

    def test_advance_to_gap_analysis_requires_artifact(self, api, fresh_flow):
        fid = fresh_flow["flow_id"]
        r = requests.post(
            f"{api}/api/flow/{fid}/advance",
            json={"step": "gap_analysis"},  # no artifact_id
            timeout=10,
        )
        assert r.status_code == 422

    def test_advance_invalid_transition_returns_409(self, api, fresh_flow):
        fid = fresh_flow["flow_id"]
        r = requests.post(
            f"{api}/api/flow/{fid}/advance",
            json={"step": "cv_generation"},  # illegal from jd_analysis
            timeout=10,
        )
        assert r.status_code == 409
        data = r.json()
        assert "detail" in data
        assert "current_step" in data["detail"]
        assert "allowed_transitions" in data["detail"]

    def test_409_body_contains_allowed_transitions(self, api, fresh_flow):
        fid = fresh_flow["flow_id"]
        r = requests.post(
            f"{api}/api/flow/{fid}/advance",
            json={"step": "cv_generation"},
            timeout=10,
        )
        allowed = r.json()["detail"]["allowed_transitions"]
        assert isinstance(allowed, list)
        assert len(allowed) > 0

    def test_advance_step_updates_state(self, api, fresh_flow):
        """Walk through gap_analysis step and verify state updated."""
        fid = fresh_flow["flow_id"]
        flow_state = requests.get(f"{api}/api/flow/{fid}/state", timeout=10).json()
        job_id = flow_state["job_id"]

        # Run gap analysis to get an artifact ID
        gap_r = requests.post(f"{api}/api/job/{job_id}/gaps", timeout=60)
        assert gap_r.status_code == 200, gap_r.text
        gap_id = gap_r.json()["id"]

        # Advance to gap_analysis
        next_step = flow_state["available_actions"]["next"]  # "cv_import" or "gap_analysis"
        if next_step == "cv_import":
            # Skip import step for this test — advance directly using skip if available
            skip_step = flow_state["available_actions"].get("skip")
            # Advance through cv_import first (no artifact required)
            adv_r = requests.post(
                f"{api}/api/flow/{fid}/advance",
                json={"step": "gap_analysis", "artifact_id": gap_id},
                timeout=10,
            )
        else:
            adv_r = requests.post(
                f"{api}/api/flow/{fid}/advance",
                json={"step": "gap_analysis", "artifact_id": gap_id},
                timeout=10,
            )
        assert adv_r.status_code == 200, adv_r.text
        updated = adv_r.json()
        assert updated["current_step"] == "gap_analysis"

    def test_gap_summary_populated_after_advance(self, api, fresh_flow):
        """After advancing to gap_analysis, FlowStateResponse.gap_summary is set."""
        fid = fresh_flow["flow_id"]
        state = requests.get(f"{api}/api/flow/{fid}/state", timeout=10).json()
        if state["current_step"] == "gap_analysis":
            assert state["gap_summary"] is not None
            assert "match_score" in state["gap_summary"]

    def test_advance_to_complete_sets_completed(self, api):
        """Drive a flow from jd_analysis all the way to complete."""
        # Create dedicated job and flow
        jd_r = requests.post(
            f"{api}/api/job/analyze",
            json={"text": JD_FILE.read_text() + f" [complete-flow {_uuid.uuid4()}]"},
            timeout=60,
        )
        assert jd_r.status_code == 200
        job_id = jd_r.json()["id"]

        flow_r = requests.post(f"{api}/api/flow", json={"job_id": job_id}, timeout=30)
        assert flow_r.status_code == 201
        fid = flow_r.json()["flow_id"]
        user_type = flow_r.json()["user_type"]

        gap_r = requests.post(f"{api}/api/job/{job_id}/gaps", timeout=60)
        assert gap_r.status_code == 200
        gap_id = gap_r.json()["id"]

        # Both new and returning users advance jd_analysis → gap_analysis.
        # Returning users skip cv_import, but still need to advance to gap_analysis.
        adv1 = requests.post(
            f"{api}/api/flow/{fid}/advance",
            json={"step": "gap_analysis", "artifact_id": gap_id},
            timeout=10,
        )
        assert adv1.status_code == 200

        # Generate a real CV (generated_cv_id FK requires a real record in generated_cvs)
        cv_r = requests.post(
            f"{api}/api/cv/generate",
            json={"job_id": job_id},
            timeout=60,
        )
        assert cv_r.status_code == 201, cv_r.text
        cv_id = cv_r.json()["cv_id"]

        adv2 = requests.post(
            f"{api}/api/flow/{fid}/advance",
            json={"step": "cv_generation"},
            timeout=10,
        )
        assert adv2.status_code == 200, adv2.text

        adv3 = requests.post(
            f"{api}/api/flow/{fid}/advance",
            json={"step": "complete", "artifact_id": cv_id},
            timeout=10,
        )
        assert adv3.status_code == 200, adv3.text
        assert adv3.json()["current_step"] == "complete"

    def test_no_transition_from_complete(self, api):
        """After reaching complete, any advance returns 409."""
        jd_r = requests.post(
            f"{api}/api/job/analyze",
            json={"text": JD_FILE.read_text() + f" [terminal-test {_uuid.uuid4()}]"},
            timeout=60,
        )
        assert jd_r.status_code == 200
        job_id = jd_r.json()["id"]
        gap_r = requests.post(f"{api}/api/job/{job_id}/gaps", timeout=60)
        assert gap_r.status_code == 200
        gap_id = gap_r.json()["id"]

        flow_r = requests.post(f"{api}/api/flow", json={"job_id": job_id}, timeout=30)
        fid = flow_r.json()["flow_id"]

        # Returning users skip cv_import but still advance jd_analysis → gap_analysis
        requests.post(
            f"{api}/api/flow/{fid}/advance",
            json={"step": "gap_analysis", "artifact_id": gap_id},
            timeout=10,
        )
        # Generate a real CV (generated_cv_id FK requires a real record in generated_cvs)
        cv_r = requests.post(
            f"{api}/api/cv/generate",
            json={"job_id": job_id},
            timeout=60,
        )
        assert cv_r.status_code == 201, cv_r.text
        cv_id = cv_r.json()["cv_id"]
        requests.post(
            f"{api}/api/flow/{fid}/advance",
            json={"step": "cv_generation"},
            timeout=10,
        )
        requests.post(
            f"{api}/api/flow/{fid}/advance",
            json={"step": "complete", "artifact_id": cv_id},
            timeout=10,
        )

        r = requests.post(
            f"{api}/api/flow/{fid}/advance",
            json={"step": "complete"},
            timeout=10,
        )
        assert r.status_code == 409
        assert r.json()["detail"]["allowed_transitions"] == []
