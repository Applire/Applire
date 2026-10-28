# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D-3b / D-12 / D-12b (Hazelnut build 5, delivery re-run). Synthetic data only.

* D-3b: the candidate answered "reached" to a task line ("Build in privacy by
  design …") and gave the outcome; the CV still printed the task. The document
  prompt view now carries such lines under a key that says they are done.
* D-12: a skill only a not_yet_reached line backs ("AI governance & risk
  management") was demanded by the JD-REQUIRED block and listed as a skill. It is
  no longer demanded, and the view moves it out of the skills list under a key
  that says it may be named only as an aim.
* D-12b: the JD-REQUIRED block asked two names of one competence to share ONE
  entry "that carries both forms"; the writer joined them with ";"."""

from __future__ import annotations

from applire.services.prompt_view import (
    NOT_YET_REACHED_SKILLS_KEY,
    REACHED_PROMPT_KEY,
    aim_only_skill_names,
    prompt_profile_view,
)

TASK = "Build in privacy by design, GDPR compliance and self-hosting."
AIM = "Leading an AI platform, establishing AI governance frameworks and risk management protocols."


def _vault() -> dict:
    return {
        "personal_info": {"name": "Jana Lehmann"},
        "work_experience": [
            {"id": "w1", "company": "Nordlicht Software", "role": "Founder", "start_date": "2026-05",
             "responsibilities": ["Develop the backend with Python and FastAPI.", TASK],
             "achievements": ["Nordlicht is privacy by design from the start: GDPR-compliant and self-hostable."]},
            {"id": "w2", "company": "Pharma Nord", "role": "Associate Director", "start_date": "2024-12",
             "responsibilities": ["Lead five tech leads; own change, validation and governance of the systems."]},
        ],
        "projects": [{"id": "p1", "name": "GenAI Platform", "description": AIM, "not_yet_reached": [AIM]}],
        "skills": [
            {"id": "s1", "name": "AI governance & risk management", "status": "confirmed"},
            {"id": "s2", "name": "Python", "status": "confirmed"},
        ],
        "metadata": {
            "aspiration_verdicts": [{"key": "k1", "text": TASK, "section": "work_experience",
                                     "entry_id": "w1", "aspiration": True, "recorded_at": "2026-10-28T00:00:00Z"}],
            "aspiration_answers": [{"key": "k1", "answer": "achieved", "entry_id": "w1",
                                    "answered_at": "2026-10-28T00:00:00Z"}],
        },
    }


def test_a_task_line_answered_reached_is_labelled_done_in_the_document_view():
    view = prompt_profile_view(_vault(), aims="label")
    w1 = view["work_experience"][0]
    assert w1[REACHED_PROMPT_KEY] == [TASK]
    assert TASK not in w1["responsibilities"]
    assert "Develop the backend with Python and FastAPI." in w1["responsibilities"]
    # the plain view (reconcile, gap) is unchanged
    assert TASK in prompt_profile_view(_vault())["work_experience"][0]["responsibilities"]


def test_a_task_answered_goal_stays_a_responsibility():
    v = _vault()
    v["metadata"]["aspiration_answers"][0]["answer"] = "goal"
    w1 = prompt_profile_view(v, aims="label")["work_experience"][0]
    assert REACHED_PROMPT_KEY not in w1 and TASK in w1["responsibilities"]


def test_a_skill_only_a_not_yet_reached_line_backs_is_named_only_as_an_aim():
    v = _vault()
    assert aim_only_skill_names(v) == {"AI governance & risk management"}
    view = prompt_profile_view(v, aims="label")
    assert [s["name"] for s in view["skills"]] == ["Python"]
    assert view[NOT_YET_REACHED_SKILLS_KEY] == ["AI governance & risk management"]


def test_a_skill_another_line_also_backs_is_a_skill():
    v = _vault()
    v["work_experience"][1]["responsibilities"].append("Set up AI governance and risk management for GxP systems.")
    assert aim_only_skill_names(v) == set()


def test_the_jd_required_block_no_longer_demands_an_aim_only_skill():
    from applire.services.cv_v2 import required_vault_skills

    job = {"required_skills": ["AI governance & risk management", "Python"], "nice_to_have_skills": [], "keywords": []}
    assert required_vault_skills(_vault(), job, None) == ["Python"]


def test_the_jd_required_block_never_asks_for_two_joined_names():
    from applire.prompts.cv_tailoring import build_v2_fields_block

    block = build_v2_fields_block({}, required_vault_skills=["AI governance frameworks", "AI governance & risk management"])
    assert "carries both forms" not in block
    assert 'never two names joined by ";"' in block
