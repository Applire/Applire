# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
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

"""F5/F6 — every listed service function accepts ``user_id`` (Strawberry W0).

ADR-092 cl. 14 + work-packages-proposal §1 F5: every public function in the
F5 module list, the background-task functions and the profile read path take
``user_id`` by keyword. W0 accepts and ignores it (zero behaviour change);
the W2 owner of each module makes it required and uses it. A new public
function in one of these modules without ``user_id`` fails here.
"""

import importlib
import inspect
import uuid

import pytest

F5_MODULES = [
    "cv",
    "cover_letter",
    "session",
    "gap",
    "review_actions",
    "review_rewrite",
    "cv_section_editor",
    "cv_assist",
    "cv_diff",
    "signature",
    "photo",
    "fact_pins",
    "matching",
    "color_detection",
    "application",
    "job",
    "documents",
    "gap_jobs",
    "flow.orchestrator",
]

BACKGROUND_TASKS = [
    ("cv", "_render_cv_background"),
    ("cv", "_update_ats_report_by_id"),
    ("cover_letter", "_render_cover_letter_background"),
    ("cover_letter", "_update_ats_report_letter_by_id"),
    ("profile.import_jobs", "run_import_job_background"),
    ("gap_jobs", "run_gap_job_background"),
]

PROFILE_READ_PATH = [
    ("profile", "get_profile_for_user"),
    ("profile.commit", "create_profile_record"),
    ("profile", "import_from_pdf"),
    ("profile", "import_from_text"),
    ("profile", "import_from_linkedin"),
    ("profile", "import_from_linkedin_zip"),
    ("profile", "import_from_linkedin_pdf"),
    ("profile", "get_profile"),
    ("profile", "profile_exists"),
    ("profile", "patch_profile_section"),
    ("profile", "get_enrichment_history"),
    ("profile", "get_profile_changes"),
    ("profile", "get_profile_health"),
    ("profile", "resolve_conflict"),
    ("profile", "resolve_confirmation"),
    ("profile", "list_open_gates"),
    ("profile", "ingest_cv"),
    ("profile", "upload_cv"),
    ("profile", "resolve_staged_extraction"),
    ("profile.snapshots", "undo_last_merge"),
    ("profile.role_add", "add_role_to_profile"),
    ("profile.reconcile.agent_bridge", "submit_agent_claims"),
    ("profile.reconcile.testimony_bridge", "submit_testimony"),
]


def _module(name: str):
    return importlib.import_module(f"applire.services.{name}")


def _public_functions(mod) -> list[tuple[str, object]]:
    return [
        (name, obj)
        for name, obj in vars(mod).items()
        if not name.startswith("_")
        and inspect.isfunction(obj)
        and obj.__module__ == mod.__name__
    ]


def _f5_cases():
    cases = []
    for modname in F5_MODULES:
        for name, fn in _public_functions(_module(modname)):
            cases.append(pytest.param(fn, id=f"{modname}.{name}"))
    for modname, name in BACKGROUND_TASKS + PROFILE_READ_PATH:
        cases.append(pytest.param(getattr(_module(modname), name), id=f"{modname}.{name}"))
    return cases


CASES = _f5_cases()


def test_enumeration_is_not_vacuous():
    # 117 public F5 functions gained the keyword in W0, ~25 already had user_id,
    # plus the background tasks and the profile read path.
    assert len(CASES) >= 160, len(CASES)
    for modname in F5_MODULES:
        assert _public_functions(_module(modname)), modname


@pytest.mark.parametrize("fn", CASES)
def test_accepts_user_id_by_keyword(fn):
    params = inspect.signature(fn).parameters
    assert "user_id" in params, f"{fn.__module__}.{fn.__qualname__} lacks user_id"
    assert params["user_id"].kind in (
        inspect.Parameter.KEYWORD_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )


def test_pure_helpers_ignore_user_id():
    """W0 contract: passing user_id changes nothing (spot check on pure helpers)."""
    from applire.services import color_detection, cv, signature
    from applire.services.flow import orchestrator

    uid = uuid.uuid4()
    assert color_detection.derive_tint("#336699", user_id=uid) == color_detection.derive_tint("#336699")
    assert cv.filename_part("Acme AG", user_id=uid) == cv.filename_part("Acme AG")
    assert signature.format_place_date("Berlin", "de", user_id=uid) == signature.format_place_date(
        "Berlin", "de"
    )
    assert orchestrator.unrecordable_artifact_notice("cv", user_id=uid) == (
        orchestrator.unrecordable_artifact_notice("cv")
    )
