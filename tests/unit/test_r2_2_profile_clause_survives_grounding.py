# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""R2-2 (Hazelnut build 4) — the honest chip shape the prompt asks for survives
the deterministic grounding filter, and the shape the model wrote in rule-arm
run 1 does not (which is why the prompt now names the join).

``filter_ungrounded_choices`` exempts a declared ``denied_terms`` concept from
term evidence only when the chip carries a ``_PIVOT_MARKERS`` phrase (#451, a
fact precondition). The prompt's "..., but my profile does not yet document X"
/ "..., aber zu X steht in meinem Profil noch nichts" carries one; the drafted
"...; zu X steht in meinem Profil noch nichts" (captured, emma run 1) does not.
Nothing in the filter changed (founder: PROMPT RULE ONLY).
"""

import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.choice_grounding import filter_ungrounded_choices  # noqa: E402

_PROFILE = {
    "skills": [{"name": "Power BI", "category": "technical"}],
    "work_experience": [{
        "company": "Schwarzwald Präzision GmbH", "role": "Financial Controller",
        "responsibilities": ["Aufbau von Power-BI-Dashboards für das Management-Reporting"],
        "technologies": ["Power BI"],
    }],
}
_CLUSTER = {"id": "c", "label": "Reporting", "gaps": ["Self-Service-Dashboards"],
            "jd_skills": ["Self-Service-Dashboards"], "jd_context": ""}


def _chip(text):
    return {"text": text, "level": "partial", "denied_terms": ["Self-Service-Dashboards"]}


def test_the_prompt_shape_survives():
    chip = _chip("Ich habe Power-BI-Dashboards für das Management-Reporting aufgebaut, "
                 "aber zu Self-Service-Dashboards steht in meinem Profil noch nichts.")
    assert filter_ungrounded_choices([chip], _CLUSTER, _PROFILE, "C") == [chip["text"]]


def test_the_english_prompt_shape_survives():
    chip = _chip("I built Power BI dashboards for management reporting, but my profile "
                 "does not yet document Self-Service-Dashboards.")
    assert filter_ungrounded_choices([chip], _CLUSTER, _PROFILE, "C") == [chip["text"]]


def test_the_semicolon_shape_the_model_drafted_does_not():
    chip = _chip("Ich habe Power-BI-Dashboards für das Management-Reporting aufgebaut; "
                 "zu Self-Service-Dashboards steht in meinem Profil noch nichts.")
    assert filter_ungrounded_choices([chip], _CLUSTER, _PROFILE, "C") is None
