# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADV-2 (Hazelnut build 5): the reviewer and every corrector round re-read the
document's source view, never the writer's system prompt. ``not_yet_reached`` is
therefore labelled ONCE, in the document prompt view, and reaches every round
from there (no corrector prompt carries a copy of the writer rule).

Measured end to end on the founder's 10-27 input (full CV + letter pipeline,
gpt-6-luna, thinking off, n=5 per arm): numbers in the WP-B report.
"""

from __future__ import annotations

import json
import pathlib

from applire.services import delivery_actions
from applire.services.prompt_view import NOT_YET_REACHED_PROMPT_KEY, prompt_profile_view

GOAL = "Establish a shift handover checklist for all three sites."
SERVICES = pathlib.Path(delivery_actions.__file__).resolve().parent


def _profile() -> dict:
    return {
        "work_experience": [
            {"id": "w1", "company": "Example GmbH", "responsibilities": [GOAL], "not_yet_reached": [GOAL]},
            {"id": "w2", "company": "Other AG", "responsibilities": ["Ran audits."], "not_yet_reached": []},
        ],
        "projects": [{"id": "p1", "name": "Platform", "responsibilities": [GOAL], "not_yet_reached": [GOAL]}],
    }


def test_the_document_view_labels_the_field_and_the_default_view_does_not():
    raw = _profile()
    labelled = prompt_profile_view(raw, aims="label")
    assert labelled["work_experience"][0][NOT_YET_REACHED_PROMPT_KEY] == [GOAL]
    assert "not_yet_reached" not in labelled["work_experience"][0]
    assert labelled["projects"][0][NOT_YET_REACHED_PROMPT_KEY] == [GOAL]
    # an empty list stays absent rather than labelled
    assert not any(k.startswith("not_yet_reached") for k in labelled["work_experience"][1])
    # the caller's profile is untouched; reconcile/gap prompts keep the plain key
    assert raw["work_experience"][0]["not_yet_reached"] == [GOAL]
    assert prompt_profile_view(raw)["work_experience"][0]["not_yet_reached"] == [GOAL]
    # the writer rules name the field — the label starts with that name
    assert NOT_YET_REACHED_PROMPT_KEY.startswith("not_yet_reached ")
    assert NOT_YET_REACHED_PROMPT_KEY in json.dumps(labelled, ensure_ascii=False)  # as every prompt dumps it


def test_every_document_source_view_is_built_labelled():
    """The CV view feeds the writer, the segmented fallback and ``source_material``
    (reviewer + every corrector round); the letter's two views feed its writer
    and its ``grounding_source`` (reviewer + corrector)."""
    cv = (SERVICES / "cv.py").read_text(encoding="utf-8")
    assert 'prompt_profile = prompt_profile_view(profile_json, aims="label")' in cv
    assert "source_material = _json.dumps(prompt_profile" in cv
    letter = (SERVICES / "cover_letter.py").read_text(encoding="utf-8")
    assert letter.count('aims="label"') == 2


def test_the_narrow_action_excerpt_carries_the_label():
    excerpt = delivery_actions._profile_excerpt(_profile(), {"source_experience_id": "w1"})
    assert f"{NOT_YET_REACHED_PROMPT_KEY}:\n- {GOAL}" in excerpt
