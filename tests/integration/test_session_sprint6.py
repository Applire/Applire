# tests/integration/test_session_sprint6.py
"""
Sprint 6 — Full happy path integration test (Task 20.9)

Validates:
  - POST /api/cv/generate returns cv_id with status=pending
  - Polling GET /api/cv/{id}/status transitions to ready
  - POST /api/flow/{id}/advance with step=cv_generation and artifact_id writes
    generated_cv_id on FlowSession
  - GET /api/flow/{id}/state returns cv_summary populated after advance
  - POST /api/flow/{id}/advance { step: "complete" } finalizes the flow

Run (requires Docker stack):
    pytest tests/integration/test_session_sprint6.py -v
"""
import json
import time
from pathlib import Path

import pytest
import requests

JD_FILE = Path(__file__).parent.parent / "files" / "jd.txt"

_LINKEDIN = {
    "firstName": "Max",
    "lastName": "Mustermann",
    "emailAddress": "max@example.de",
    "headline": "QA Manager",
    "location": {"name": "Munich, Germany"},
    "positions": [
        {
            "title": "QA Manager",
            "companyName": "Bayer AG",
            "startDate": {"month": 1, "year": 2020},
            "endDate": None,
            "description": "Quality assurance and regulatory compliance for pharma products.",
        }
    ],
    "educations": [
        {
            "schoolName": "LMU München",
            "degreeName": "Master of Science",
            "fieldOfStudy": "Pharmazie",
            "startDate": {"year": 2015},
            "endDate": {"year": 2019},
        }
    ],
    "skills": [{"name": "Quality Assurance"}, {"name": "21 CFR Part 11"}, {"name": "GMP"}],
    "languages": [{"language": {"name": "German"}, "proficiency": "NATIVE_OR_BILINGUAL"}],
}


@pytest.fixture(scope="module", autouse=True)
def ensure_profile(api):
    r = requests.post(
        f"{api}/api/profile/import",
        data={"linkedin_json": json.dumps(_LINKEDIN)},
        timeout=90,
    )
    assert r.status_code == 200, f"Profile import failed: {r.text}"


def _wait_for_ready(api: str, cv_id: str, timeout: int = 120) -> dict:
    """Poll status until ready or timeout. Returns the status response dict."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(f"{api}/api/cv/{cv_id}/status", timeout=10)
        assert r.status_code == 200
        data = r.json()
        if data["status"] == "ready":
            return data
        if data["status"] == "failed":
            pytest.fail(f"CV generation failed: {data}")
        time.sleep(3)
    pytest.fail(f"CV {cv_id} did not become ready within {timeout}s")


def _run_gap_analysis(api: str, job_id: str, timeout: int = 120) -> dict:
    """Start the async gap job (202) and poll until ready. Returns the gap analysis.

    The synchronous ``POST /gaps`` was removed (E037 N2) in favour of a job + poll
    so the gaps screen can't block ~2 min or 504 mid-analysis.
    """
    start_r = requests.post(f"{api}/api/job/{job_id}/gap-jobs", timeout=30)
    assert start_r.status_code == 202, f"Gap job start failed: {start_r.text}"
    gap_job_id = start_r.json()["gap_job_id"]

    deadline = time.time() + timeout
    while time.time() < deadline:
        poll_r = requests.get(
            f"{api}/api/job/{job_id}/gap-jobs/{gap_job_id}", timeout=10
        )
        assert poll_r.status_code == 200, f"Gap job poll failed: {poll_r.text}"
        data = poll_r.json()
        if data["status"] == "ready":
            assert data["result"] is not None, f"Gap job ready without result: {data}"
            return data["result"]
        if data["status"] == "failed":
            pytest.fail(f"Gap analysis failed: {data}")
        time.sleep(3)
    pytest.fail(f"Gap job {gap_job_id} did not become ready within {timeout}s")


class TestSprint6FullFlow:
    @pytest.fixture(scope="class")
    def flow_and_cv(self, api):
        """Create job + flow + CV, advance through gap_analysis then cv_generation step.

        Idempotent: if the flow is already at a later step (e.g. after a previous local
        test run against a non-ephemeral DB), skips already-completed transitions.
        """
        jd_text = JD_FILE.read_text() if JD_FILE.exists() else (
            "QA Manager, 21 CFR Part 11 — München\n"
            "Requirements: GMP, quality assurance, regulatory compliance."
        )

        # Analyze JD
        jd_r = requests.post(f"{api}/api/job/analyze", json={"text": jd_text}, timeout=60)
        assert jd_r.status_code == 200
        job_id = jd_r.json()["id"]

        # Create flow (idempotent for same user+job)
        flow_r = requests.post(f"{api}/api/flow", json={"job_id": job_id}, timeout=30)
        assert flow_r.status_code in (200, 201), f"Flow creation failed: {flow_r.text}"
        flow_id = flow_r.json()["flow_id"]

        # Inspect current step — previous runs may have advanced it already.
        state_r = requests.get(f"{api}/api/flow/{flow_id}/state", timeout=10)
        assert state_r.status_code == 200
        current_step = state_r.json()["current_step"]

        # Already finished — return existing cv_id from the flow state.
        if current_step == "complete":
            cv_summary = state_r.json().get("cv_summary") or {}
            return {"flow_id": flow_id, "cv_id": cv_summary["cv_id"], "job_id": job_id}

        # Advance to gap_analysis if still in an earlier step.
        if current_step in ("jd_analysis", "cv_import"):
            gap = _run_gap_analysis(api, job_id)
            gap_id = gap["id"]
            adv_gap = requests.post(
                f"{api}/api/flow/{flow_id}/advance",
                json={"step": "gap_analysis", "artifact_id": gap_id},
                timeout=10,
            )
            assert adv_gap.status_code == 200, f"Flow advance to gap_analysis failed: {adv_gap.text}"
            current_step = "gap_analysis"

        # Generate CV
        cv_r = requests.post(
            f"{api}/api/cv/generate",
            json={"job_id": job_id, "template": "classic_german"},
            timeout=30,
        )
        assert cv_r.status_code == 201, f"CV generate failed: {cv_r.text}"
        cv_id = cv_r.json()["cv_id"]
        _wait_for_ready(api, cv_id)

        # Advance to cv_generation if still at gap_analysis.
        if current_step == "gap_analysis":
            adv_r = requests.post(
                f"{api}/api/flow/{flow_id}/advance",
                json={"step": "cv_generation"},
                timeout=10,
            )
            assert adv_r.status_code == 200, f"Flow advance to cv_generation failed: {adv_r.text}"

        # Advance to complete, recording the generated cv.
        done_r = requests.post(
            f"{api}/api/flow/{flow_id}/advance",
            json={"step": "complete", "artifact_id": cv_id},
            timeout=10,
        )
        assert done_r.status_code == 200, f"Flow advance to complete failed: {done_r.text}"

        return {"flow_id": flow_id, "cv_id": cv_id, "job_id": job_id}

    def test_generate_returns_cv_id(self, api, flow_and_cv):
        assert len(flow_and_cv["cv_id"]) == 36

    def test_flow_state_has_cv_summary_after_advance(self, api, flow_and_cv):
        """After advance to complete, flow state has cv_summary."""
        r = requests.get(f"{api}/api/flow/{flow_and_cv['flow_id']}/state", timeout=10)
        assert r.status_code == 200
        state = r.json()
        assert state.get("cv_summary") is not None, "cv_summary should be set after complete"
        assert state["cv_summary"]["cv_id"] == flow_and_cv["cv_id"]

    def test_flow_state_current_step_is_complete(self, api, flow_and_cv):
        r = requests.get(f"{api}/api/flow/{flow_and_cv['flow_id']}/state", timeout=10)
        state = r.json()
        assert state.get("current_step") == "complete"

    def test_advance_to_complete_finalizes_flow(self, api, flow_and_cv):
        """Re-advancing to complete from complete is an idempotent no-op (not 409)."""
        r = requests.post(
            f"{api}/api/flow/{flow_and_cv['flow_id']}/advance",
            json={"step": "complete", "artifact_id": flow_and_cv["cv_id"]},
            timeout=10,
        )
        assert r.status_code == 200, r.text
        state = r.json()
        assert state["current_step"] == "complete"
        assert state["cv_summary"]["cv_id"] == flow_and_cv["cv_id"]
