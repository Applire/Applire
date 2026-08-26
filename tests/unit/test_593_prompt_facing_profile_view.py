# Copyright (C) 2024-2026 Tobias Rosenbaum
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

"""#593 / ADR-078 — the vault's bookkeeping does not ride into a prompt.

Three tiers, because the function being right proves nothing about the seams:

1. **The view** — allowlist semantics, copy semantics, malformed tolerance.
2. **Clause 6's totality** — every field of ``ProfileMetadata`` is classified,
   so a field added later fails this suite until somebody decides which it is.
   This is the control that keeps the allowlist true over time rather than at
   the moment it was written.
3. **The seams** — each prompt is driven through its REAL service path with a
   mocked provider and the prompt text is captured and asserted on. A test of
   ``prompt_profile_view`` alone would be a control that cannot fire: the
   defect was never "the function is wrong", it was "nothing called it".

The size bound at the end is the regression test that would have caught the
original defect: on the real dev profile the CV writer prompt measured 211,507
chars, of which 138,946 were ``metadata.enrichment_history``.
"""

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.prompt_view import (  # noqa: E402
    PROMPT_EXCLUDED_METADATA_KEYS,
    PROMPT_EXCLUDED_TOP_LEVEL_KEYS,
    PROMPT_FACING_METADATA_KEYS,
    prompt_profile_view,
)

# A receipt phrase that appears ONLY inside the audit trail. Every seam test
# asserts this string is absent from the prompt it captured — a marker chosen
# so that a partially-applied filter (metadata kept, history trimmed) fails
# just as loudly as no filter at all.
RECEIPT_MARKER = "RECEIPT-ONLY-TOKEN-8f21"
DENIAL_MARKER = "no hands-on Kubernetes experience"


def _enrichment_record(i: int) -> dict:
    return {
        "id": f"rec-{i}",
        "timestamp": "2026-08-26T06:09:01Z",
        "source": "manual_edit",
        "changes": [
            {
                "section": "work_experience",
                "field": "achievements",
                "action": "updated",
                "old_value": f"{RECEIPT_MARKER} old value number {i} " + ("x" * 200),
                "new_value": f"{RECEIPT_MARKER} new value number {i} " + ("y" * 200),
                "rationale": "user edit",
            }
        ],
    }


def _profile_json(history: int = 3) -> dict:
    return {
        "personal_info": {"name": "Anna Bauer", "email": "anna@example.com"},
        "professional_summary": {"en": "Engineering leader shipping production systems."},
        "work_experience": [
            {
                "id": "w-datacore",
                "company": "DataCore Systems",
                "role": "Platform Engineering Lead",
                "start_date": "2015-01",
                "end_date": None,
                "is_current": True,
                "responsibilities": ["Built and maintained REST APIs in FastAPI."],
                "achievements": ["Cut checkout latency by 70%."],
            },
        ],
        "projects": [],
        "skills": [],
        "education": [],
        "languages": [],
        "certifications": [],
        "publications": [],
        "volunteer_activities": [],
        "signature_stories": [],
        "_meta": {"na_fields": ["personal_info.photo_url"]},
        "metadata": {
            "completeness_score": 0.8,
            "created_via": "cv_upload",
            "created_at": "2026-01-01T00:00:00Z",
            "last_updated": "2026-08-26T06:09:01Z",
            "application_count": 1,
            "enrichment_history": [_enrichment_record(i) for i in range(history)],
            # Schema-valid shapes throughout: the seam tests below round-trip this
            # fixture through `MasterProfileData`, and a fixture the production
            # model rejects would prove nothing about production
            # (`feedback_realism_of_inputs_beats_assertion_strength`).
            "pending_conflicts": [
                {
                    "conflict_id": "c-1",
                    "section": "work_experience",
                    "field": "role",
                    "existing_value": f"{RECEIPT_MARKER} existing",
                    "incoming_value": f"{RECEIPT_MARKER} incoming",
                    "source": "cv_upload",
                }
            ],
            "pending_confirmations": [
                {
                    "confirmation_id": "pc-1",
                    "question": f"{RECEIPT_MARKER} which role owns this?",
                    "options": ["a", "b"],
                    "source": "cv_upload",
                }
            ],
            "denied_concepts": [
                {
                    "concept": "Kubernetes",
                    "statement": DENIAL_MARKER,
                    "source": "interview",
                    "date": "2026-08-20",
                }
            ],
        },
    }


# ─────────────────────────── tier 1 — the view ────────────────────────────


def test_the_allowlisted_key_survives_and_every_bookkeeping_key_is_dropped():
    view = prompt_profile_view(_profile_json())
    assert set(view["metadata"]) == {"denied_concepts"}
    assert view["metadata"]["denied_concepts"][0]["statement"] == DENIAL_MARKER
    assert RECEIPT_MARKER not in json.dumps(view, ensure_ascii=False)


def test_the_candidates_content_is_untouched():
    """The view filters bookkeeping ONLY — a content key it does not know about
    must pass through, or this becomes a second `exclude_unconfirmed`."""
    src = _profile_json()
    view = prompt_profile_view(src)
    for key in src:
        if key in PROMPT_EXCLUDED_TOP_LEVEL_KEYS:
            continue
        assert key in view, f"content key {key!r} was dropped by the prompt view"
    assert view["work_experience"] == src["work_experience"]
    assert view["personal_info"] == src["personal_info"]


def test_the_meta_sidecar_is_dropped():
    assert "_meta" not in prompt_profile_view(_profile_json())


def test_the_metadata_key_is_REMOVED_not_emptied_when_nothing_survives():
    """ADR-078 clause 2. An empty ``metadata: {}`` is noise in the prompt AND
    reads as a different claim — "this candidate has no history"."""
    src = _profile_json()
    del src["metadata"]["denied_concepts"]
    view = prompt_profile_view(src)
    assert "metadata" not in view


def test_the_narrowing_hook_drops_metadata_entirely():
    """Clause 4 — what ``prompts/gap_analysis`` passes (the F4 reason: a
    denial's own text token-matches FOR the skill it denies)."""
    view = prompt_profile_view(_profile_json(), keep=frozenset())
    assert "metadata" not in view
    assert DENIAL_MARKER not in json.dumps(view, ensure_ascii=False)


def test_the_input_is_never_mutated():
    """The candidate's persisted profile is a live ORM attribute at three of
    the four call sites; a filter that mutated it would be a vault write."""
    src = _profile_json()
    before = json.dumps(src, sort_keys=True, ensure_ascii=False)
    prompt_profile_view(src)
    assert json.dumps(src, sort_keys=True, ensure_ascii=False) == before


@pytest.mark.parametrize(
    "value", [None, [], "", 0, {"metadata": None}, {"metadata": "wat"}, {"metadata": []}]
)
def test_malformed_shapes_never_raise(value):
    """A prompt-INPUT filter must never become a new way for generation to
    fail — the fail-safe-by-construction rule the vault-evidence block has."""
    out = prompt_profile_view(value)
    if isinstance(value, dict):
        assert "metadata" not in out


def test_a_profile_with_no_metadata_at_all_is_unchanged():
    src = {"work_experience": [], "skills": []}
    assert prompt_profile_view(src) == src


# ───────────────────── tier 2 — clause 6, the totality ─────────────────────


def test_every_profile_metadata_field_is_classified():
    """ADR-078 clause 6 — the control that makes clause 2 hold over TIME.

    If this fails you have added a field to ``ProfileMetadata``: decide whether
    a prompt may see it. Prompt-facing → ``PROMPT_FACING_METADATA_KEYS``, and
    name the prompt that reads it in the constant's docstring (clause 3).
    Bookkeeping → ``PROMPT_EXCLUDED_METADATA_KEYS``. Do not "fix" this test by
    deleting the assertion: an unclassified field defaults to riding into every
    prompt the vault feeds, which is the entire defect of #593.
    """
    from applire.schemas.profile import ProfileMetadata

    declared = set(ProfileMetadata.model_fields)
    classified = PROMPT_FACING_METADATA_KEYS | PROMPT_EXCLUDED_METADATA_KEYS
    assert declared - classified == set(), (
        f"unclassified ProfileMetadata field(s): {sorted(declared - classified)}"
    )
    assert classified - declared == set(), (
        f"classified key(s) that no longer exist on ProfileMetadata: "
        f"{sorted(classified - declared)}"
    )


def test_the_two_classifications_are_disjoint():
    assert PROMPT_FACING_METADATA_KEYS & PROMPT_EXCLUDED_METADATA_KEYS == frozenset()


def test_the_enrichment_history_is_classified_as_bookkeeping():
    """The founding case, pinned by name: 138,946 of 144,624 chars."""
    assert "enrichment_history" in PROMPT_EXCLUDED_METADATA_KEYS
    assert "enrichment_history" not in PROMPT_FACING_METADATA_KEYS


# ───────────── tier 3 — the seams (controls that must FIRE) ──────────────
#
# Each of these drives the REAL service path with a mocked provider and asserts
# on the prompt text that path actually produced. Nothing here trusts that a
# call site exists — the whole class of defect #593 belongs to is "the function
# was right and nothing called it".


def _job_mock():
    job = MagicMock()
    job.role_title = "Senior Backend Engineer"
    job.required_skills = ["REST APIs"]
    job.nice_to_have_skills = []
    job.keywords = []
    job.seniority_level = "senior"
    job.company_culture_signals = []
    job.language_requirement = "en"
    job.raw_text = "We are looking for a Senior Backend Engineer."
    job.jd_language = "en"
    job.company_name = "Vector Analytics"
    job.leadership_emphasis = None
    return job


def _tailored_raw() -> dict:
    return {
        "contact": {"name": "Anna Bauer", "email": "anna@example.com", "phone": None,
                    "location": None, "linkedin": None},
        "summary": "Engineering leader.",
        "work_history": [{"company": "DataCore Systems", "role": "Platform Engineering Lead",
                          "start_date": "2015-01", "end_date": None, "bullets": []}],
        "skills": [], "education": [], "languages": [],
    }


async def _run_cv(profile_json: dict) -> dict:
    """Drive ``_render_cv_background`` and capture what reached the model."""
    cv_id, job_id, profile_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    mock_cv = MagicMock(); mock_cv.status = "pending"; mock_cv.target_pages = 2
    mock_profile = MagicMock(); mock_profile.profile_json = profile_json
    mock_gap = MagicMock()
    mock_gap.keyword_gaps = []; mock_gap.critical_gaps = []; mock_gap.keyword_ledger = []
    mock_job = _job_mock()

    mock_db = AsyncMock()
    mock_db.get.side_effect = lambda model, id_: {
        cv_id: mock_cv, job_id: mock_job, profile_id: mock_profile,
    }[id_]
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_gap
    mock_db.execute.return_value = mock_result

    captured: dict = {}

    async def fake_writer(*args, **kwargs):
        # `profile` is the SECOND positional argument (cv.py:2774-2777).
        captured["writer_profile"] = args[1] if len(args) > 1 else kwargs.get("profile")
        return _tailored_raw()

    async def fake_loop(**kw):
        captured.setdefault("loop_sources", []).append(kw.get("source"))
        return kw["draft"]

    with patch("applire.services.cv.AsyncSessionLocal") as mock_session_local, \
         patch("applire.services.cv.get_provider", return_value=AsyncMock()), \
         patch("applire.services.cv._tailor_cv_with_fallback", side_effect=fake_writer), \
         patch("applire.services.cv.review_and_refine", new=AsyncMock(side_effect=fake_loop)), \
         patch("applire.services.cv._review_cv_language",
               new=AsyncMock(side_effect=lambda draft, *a, **kw: draft)), \
         patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"pdf")), \
         patch("applire.services.cv_section_editor.build_content_snapshot", return_value={}):
        mock_session_local.return_value.__aenter__.return_value = mock_db
        from applire.services.cv import _render_cv_background
        await _render_cv_background(cv_id, job_id, profile_id, "classic_german")
    return captured


@pytest.mark.asyncio
async def test_SEAM_cv_writer_gets_the_content_and_not_the_audit_trail():
    captured = await _run_cv(_profile_json())
    profile = captured["writer_profile"]
    assert profile is not None, "the CV writer was never called"
    assert "enrichment_history" not in json.dumps(profile, ensure_ascii=False)
    assert RECEIPT_MARKER not in json.dumps(profile, ensure_ascii=False)
    # …and the content is still all there, or we have broken generation.
    assert profile["work_experience"][0]["company"] == "DataCore Systems"
    assert profile["metadata"]["denied_concepts"][0]["statement"] == DENIAL_MARKER


@pytest.mark.asyncio
async def test_SEAM_cv_review_loop_source_carries_no_receipts():
    """`source_material` is handed to the reviewer AND the corrector unchanged
    every round (§5.3.23), so a receipt in it is paid for on each of them."""
    captured = await _run_cv(_profile_json())
    sources = [s for s in captured.get("loop_sources", []) if s]
    assert sources, "review_and_refine was never called with a source"
    for source in sources:
        assert RECEIPT_MARKER not in source
        assert "enrichment_history" not in source


@pytest.mark.asyncio
async def test_SEAM_the_stated_limits_block_still_reaches_the_loop_source():
    """The negative control for the whole change: the view must not starve the
    DETERMINISTIC readers of `metadata`. `collect_stated_limits` reads
    `denied_concepts` off the unfiltered profile BEFORE the view is built, and
    its rendered block is folded into the same `source_material`."""
    sources = [s for s in (await _run_cv(_profile_json())).get("loop_sources", []) if s]
    assert any(DENIAL_MARKER in s for s in sources), (
        "the candidate's verbatim stated limit no longer reaches the review loop — "
        "the prompt view was applied too early, ahead of a deterministic reader"
    )


@pytest.mark.asyncio
async def test_SEAM_a_profile_with_no_metadata_still_generates():
    src = _profile_json()
    del src["metadata"]
    captured = await _run_cv(src)
    assert captured["writer_profile"]["work_experience"][0]["id"] == "w-datacore"


def test_SEAM_reconcile_prompt_carries_no_audit_trail():
    """The heaviest instance: a vault WRITE path, and `reconcile` is called once
    per interview turn, per testimony, per agent claim, per import slice."""
    from applire.prompts.reconcile import build_reconcile_prompt
    from applire.schemas.profile import MasterProfileData

    profile = MasterProfileData.model_validate(_profile_json())
    prompt = build_reconcile_prompt(profile, {"skills": ["FastAPI"]}, "cv_upload")
    assert RECEIPT_MARKER not in prompt
    assert "enrichment_history" not in prompt
    # The entity ids the prompt exists to let the model target must survive.
    assert "w-datacore" in prompt


def test_SEAM_gap_prompt_excludes_metadata_entirely_including_denials():
    """Clause 4's narrowing — the F4 property, pinned. A denial's own text
    token-matches FOR the skill it denies, so this chain sees no `metadata`
    at all, denials included."""
    from applire.prompts.gap_analysis import build_user_prompt

    from applire.services.gap_inference import PreClassification

    prompt = build_user_prompt(
        {"role_title": "Senior Backend Engineer", "required_skills": ["Kubernetes"],
         "nice_to_have_skills": [], "keywords": []},
        _profile_json(),
        PreClassification(),
    )
    profile_block = prompt.split("CANDIDATE PROFILE:")[1]
    assert '"metadata"' not in profile_block
    assert RECEIPT_MARKER not in profile_block
    assert DENIAL_MARKER not in profile_block
    assert "w-datacore" in profile_block


# ──────────────── tier 4 — the size bound (the regression) ────────────────


@pytest.mark.asyncio
async def test_the_prompt_does_not_grow_with_the_audit_trail():
    """THE regression test for #593.

    A profile whose trail has grown to ~200 receipts — the shape a power user
    reaches after months, and roughly twice the dev profile that triggered the
    issue. The assertion is deliberately RELATIVE, not an absolute char budget:
    what broke was that prompt size tracked trail size, so the property to pin
    is that it no longer does (a fixed budget would drift with every legitimate
    prompt edit and get raised until it meant nothing — the CI-gates rule).
    """
    small, large = _profile_json(history=1), _profile_json(history=200)
    raw_small = len(json.dumps(small, ensure_ascii=False))
    raw_large = len(json.dumps(large, ensure_ascii=False))
    assert raw_large > raw_small * 20, "fixture is not actually exercising growth"

    writer_small = json.dumps((await _run_cv(small))["writer_profile"], ensure_ascii=False)
    writer_large = json.dumps((await _run_cv(large))["writer_profile"], ensure_ascii=False)

    assert writer_small == writer_large, (
        "the writer's view of the vault changed when only the AUDIT TRAIL grew — "
        f"{len(writer_small)} chars vs {len(writer_large)} on a profile that grew "
        f"{raw_small} → {raw_large}. That coupling IS #593."
    )
