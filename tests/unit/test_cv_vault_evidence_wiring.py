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

"""#303 — ``_render_cv_background`` selects the vault's strongest JD-relevant
evidence and threads it into the CV writer, the way ``generate_cover_letter``
has done for the letter since #271. Mocks the LLM provider/DB session; no
Docker, no LLM, no real network.

The defect this pins: the CV writer's only concept->evidence pointer was the
Keyword Ledger's ``evidence`` field, which is the gap classifier's free-text
``reason`` (``services.gap.ledger_input_from_classification``). It paraphrases
why the row was graded; it never quotes the vault and never names the entry
that owns the evidence. The letter chain has had the verbatim, owner-pathed
digest since #271 — which is why, across charter runs #7/13/17/18, the letter
kept naming vault sentences the CV had reduced to a bare skills keyword.
"""

import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

# The vault's own sentence — the thing a hiring reviewer looks for.
_VAULT_SENTENCE = "Budgetverantwortung ca. 6 Mio. EUR fuer Personal und Instandhaltung."
# The gap classifier's rationale, copied onto the ledger row's `evidence`.
# Deliberately shares no distinctive wording with the vault sentence.
_CLASSIFIER_REASON = "Listed as a skill and visible in the work history."


def _profile_json() -> dict:
    return {
        "work_experience": [
            {
                "id": "w1",
                "company": "Weberit",
                "role": "Produktionsleiter",
                "start_date": "2015-01",
                "end_date": None,
                "is_current": True,
                "responsibilities": [_VAULT_SENTENCE],
                "achievements": [],
            },
        ],
        "projects": [],
        "skills": [], "education": [], "languages": [],
        "personal_info": {"name": "Marcus", "email": None},
    }


def _tailored_raw() -> dict:
    return {
        "contact": {"name": "Marcus", "email": None, "phone": None,
                    "location": None, "linkedin": None},
        "summary": "Produktionsleiter.",
        "work_history": [{"company": "Weberit", "role": "Produktionsleiter",
                          "start_date": "2015-01", "end_date": None, "bullets": []}],
        "skills": [], "education": [], "languages": [],
    }


def _mocks(ledger: list[dict]):
    cv_id, job_id, profile_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    mock_cv = MagicMock()
    mock_cv.status = "pending"
    mock_cv.target_pages = 2

    mock_job = MagicMock()
    mock_job.role_title = "Werkleiter"
    mock_job.required_skills = ["Budgetverantwortung"]
    mock_job.nice_to_have_skills = []
    mock_job.keywords = []
    mock_job.seniority_level = ""
    mock_job.company_culture_signals = []
    mock_job.language_requirement = ""
    mock_job.raw_text = "Wir suchen einen Werkleiter mit Budgetverantwortung."
    mock_job.jd_language = "de"

    mock_profile = MagicMock()
    mock_profile.profile_json = _profile_json()

    mock_gap = MagicMock()
    mock_gap.keyword_gaps = []
    mock_gap.critical_gaps = []
    mock_gap.keyword_ledger = ledger

    mock_db = AsyncMock()
    mock_db.get.side_effect = lambda model, id_: {
        cv_id: mock_cv, job_id: mock_job, profile_id: mock_profile,
    }[id_]
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_gap
    mock_db.execute.return_value = mock_result
    return cv_id, job_id, profile_id, mock_db


async def _run(ledger: list[dict]) -> dict:
    cv_id, job_id, profile_id, mock_db = _mocks(ledger)
    captured: dict = {}

    async def fake_fallback(*args, **kwargs):
        captured.update(kwargs)
        return _tailored_raw()

    with patch("applire.services.cv.AsyncSessionLocal") as mock_session_local, \
         patch("applire.services.cv.get_provider", return_value=AsyncMock()), \
         patch("applire.services.cv._tailor_cv_with_fallback", side_effect=fake_fallback), \
         patch("applire.services.cv.review_and_refine",
               new=AsyncMock(side_effect=lambda **kw: kw["draft"])), \
         patch("applire.services.cv._review_cv_language",
               new=AsyncMock(side_effect=lambda draft, *a, **kw: draft)), \
         patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"pdf")), \
         patch("applire.services.cv_section_editor.build_content_snapshot", return_value={}):
        mock_session_local.return_value.__aenter__.return_value = mock_db
        from applire.services.cv import _render_cv_background
        await _render_cv_background(cv_id, job_id, profile_id, "classic_german")
    return captured


_CLAIMABLE_LEDGER = [
    {"concept": "Budgetverantwortung", "surface_forms": ["Budgetverantwortung"],
     "claimable": True, "status": "direct", "sources": ["required"],
     "fit_weight": 1.0, "evidence": _CLASSIFIER_REASON},
]


@pytest.mark.asyncio
async def test_render_cv_background_threads_the_vault_evidence_digest_into_the_writer():
    captured = await _run(_CLAIMABLE_LEDGER)
    assert "vault_evidence_block" in captured, (
        "_tailor_cv_with_fallback was not given a vault_evidence_block"
    )
    block = captured["vault_evidence_block"]
    assert block, "the digest must not be empty for a claimable, vault-backed concept"
    assert "STRONGEST VAULT EVIDENCE" in block


@pytest.mark.asyncio
async def test_the_digest_carries_the_vault_sentence_not_the_classifier_paraphrase():
    """The pinned defect. Before this fix the writer's only pointer at this
    concept's evidence was ``_CLASSIFIER_REASON``; the number a hiring reviewer
    scores the requirement on lived only in the profile dump, unlabelled."""
    block = (await _run(_CLAIMABLE_LEDGER))["vault_evidence_block"]
    assert _VAULT_SENTENCE in block
    assert _CLASSIFIER_REASON not in block


@pytest.mark.asyncio
async def test_the_digest_names_the_vault_entry_that_owns_each_item():
    """Rules 1 and 2 of the CV writer prompt are per-entry; an item without its
    owner invites the ADR-071 misattribution class."""
    block = (await _run(_CLAIMABLE_LEDGER))["vault_evidence_block"]
    assert "work_experience[0]" in block


@pytest.mark.asyncio
async def test_no_claimable_ledger_yields_no_block_and_does_not_crash():
    """Legacy/pre-E037 rows have no ledger — generation must be unchanged."""
    captured = await _run([])
    assert not captured.get("vault_evidence_block")


@pytest.mark.asyncio
async def test_segmented_path_gives_each_work_entry_only_its_own_evidence():
    """ADR-067's opening complaint is that the single-call and segmented paths
    diverged. The digest reaches both — but OWNER-SCOPED here, because a
    per-entry writer handed another employer's achievement is being invited to
    misattribute it (ADR-071 clause 1)."""
    from applire.services.vault_evidence import select_vault_evidence

    profile = {
        "work_experience": [
            {"id": "w1", "company": "Weberit", "role": "Produktionsleiter",
             "start_date": "2015-01", "end_date": None, "is_current": True,
             "responsibilities": [_VAULT_SENTENCE], "achievements": []},
            {"id": "w2", "company": "Rasselstein", "role": "Meister",
             "start_date": "2008-01", "end_date": "2014-12",
             "responsibilities": ["Betrieb der Walzstrasse mit 40 Mitarbeitenden geleitet."],
             "achievements": []},
        ],
        "projects": [], "skills": [], "education": [], "languages": [],
        "personal_info": {"name": "Marcus", "email": None},
    }
    ledger = [
        {"concept": "Budgetverantwortung", "surface_forms": ["Budgetverantwortung"],
         "claimable": True, "status": "direct", "evidence": _CLASSIFIER_REASON},
        {"concept": "Walzstrasse", "surface_forms": ["Walzstrasse"],
         "claimable": True, "status": "direct", "evidence": _CLASSIFIER_REASON},
    ]
    items = select_vault_evidence(ledger, "", profile)
    assert len(items) >= 2, "fixture must select evidence for both entries"

    prompts: list[str] = []

    class _Provider:
        async def aparse_json(self, prompt, **kwargs):
            prompts.append(prompt)
            if "tailored bullets" in prompt:
                return {"bullets": [], "projects": []}
            return {"summary": "", "skills": [], "projects": [],
                    "per_role_themes": {}, "summary_angle": ""}

    from applire.services.cv import generate_cv_segmented

    await generate_cv_segmented(
        {"role_title": "Werkleiter"}, profile, [],
        output_language="de", provider=_Provider(),
        keyword_ledger=ledger, vault_evidence_items=items,
    )

    entry_prompts = [p for p in prompts if "THIS WORK ENTRY" in p]
    assert len(entry_prompts) == 2
    weberit = next(p for p in entry_prompts if '"id": "w1"' in p)
    rasselstein = next(p for p in entry_prompts if '"id": "w2"' in p)
    # Each writer sees its own evidence in the digest block, and never the
    # other entry's — the KEYWORD LEDGER above legitimately names every
    # concept, so the assertion is scoped to the digest itself.
    def _digest(prompt: str) -> str:
        assert "=== STRONGEST VAULT EVIDENCE" in prompt
        return prompt.split("=== STRONGEST VAULT EVIDENCE", 1)[1].split("\n\n", 1)[0]

    assert _VAULT_SENTENCE in _digest(weberit)
    assert "Walzstrasse mit 40 Mitarbeitenden" not in _digest(weberit)
    assert "Walzstrasse mit 40 Mitarbeitenden" in _digest(rasselstein)
    assert _VAULT_SENTENCE not in _digest(rasselstein)
