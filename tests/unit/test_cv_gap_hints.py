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

"""Gap hints derive from the Keyword Ledger × live document coverage.

ADR-019 amended 2026-07-03 (issue #117): the CV section editor's "Related gaps"
are computed at read time as (ledger entry × current document coverage) —
covered entries are hidden, uncovered claimable entries keep the editor/Kaile
CTA, uncovered honest gaps route to enrichment. Coverage is NEVER persisted
into the gap analysis (the former save-time ``_resolve_gaps`` mutation of
``category_b``/``category_c`` is removed).
"""
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def db():
    """In-memory SQLite session with all models registered."""
    from applire.db.session import Base  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.cover_letter  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


def _entry(concept, status="partial", surface_forms=None, fit_weight=1.0):
    return {
        "concept": concept,
        "surface_forms": surface_forms if surface_forms is not None else [concept],
        "sources": ["required"],
        "fit_weight": fit_weight,
        "status": status,
        "evidence": "" if status == "gap" else f"evidence for {concept}",
        "claimable": status in ("direct", "partial"),
    }


# ---------------------------------------------------------------------------
# build_gap_hints — pure function (ledger path)
# ---------------------------------------------------------------------------

class TestBuildGapHintsLedger:
    def _flatten(self, gap_map, general):
        return [h for hints in gap_map.values() for h in hints] + list(general)

    def test_claimable_entry_covered_in_document_is_hidden(self):
        """UAT #117 regression: a keyword already in the skills list must NOT hint."""
        from applire.services.cv_gap_hints import build_gap_hints

        gap_map, general = build_gap_hints(
            ledger=[_entry("Workflow Automation")],
            category_b=["Workflow Automation"],
            category_c=[],
            section_contents={"skills": "Leadership\nWorkflow Automation\nCI/CD"},
        )
        assert self._flatten(gap_map, general) == []

    def test_claimable_entry_uncovered_is_hinted_as_claimable(self):
        from applire.services.cv_gap_hints import build_gap_hints

        gap_map, general = build_gap_hints(
            ledger=[_entry("Workflow Automation")],
            category_b=["Workflow Automation"],
            category_c=[],
            section_contents={"skills": "Leadership\nCI/CD"},
        )
        hints = self._flatten(gap_map, general)
        assert [h.label for h in hints] == ["Workflow Automation"]
        assert hints[0].kind == "claimable"

    def test_honest_gap_uncovered_is_hinted_as_honest(self):
        from applire.services.cv_gap_hints import build_gap_hints

        gap_map, general = build_gap_hints(
            ledger=[_entry("DevSecOps", status="gap")],
            category_b=[],
            category_c=["DevSecOps"],
            section_contents={"skills": "Leadership\nCI/CD"},
        )
        hints = self._flatten(gap_map, general)
        assert [h.label for h in hints] == ["DevSecOps"]
        assert hints[0].kind == "honest"

    def test_honest_gap_covered_is_not_hinted(self):
        """Present-but-unsupported is the ATS panel's truthfulness warning, not a hint."""
        from applire.services.cv_gap_hints import build_gap_hints

        gap_map, general = build_gap_hints(
            ledger=[_entry("DevSecOps", status="gap")],
            category_b=[],
            category_c=["DevSecOps"],
            section_contents={"skills": "DevSecOps\nCI/CD"},
        )
        assert self._flatten(gap_map, general) == []

    def test_any_surface_form_counts_as_coverage(self):
        from applire.services.cv_gap_hints import build_gap_hints

        gap_map, general = build_gap_hints(
            ledger=[_entry("container orchestration",
                           surface_forms=["Kubernetes", "K8s"])],
            category_b=["container orchestration"],
            category_c=[],
            section_contents={"skills": "K8s\nDocker"},
        )
        assert self._flatten(gap_map, general) == []

    def test_direct_uncovered_entry_is_hinted_as_claimable(self):
        """The ATS 'supported by your profile but not in the document' case."""
        from applire.services.cv_gap_hints import build_gap_hints

        gap_map, general = build_gap_hints(
            ledger=[_entry("Developer Experience", status="direct")],
            category_b=[],
            category_c=[],
            section_contents={"skills": "Leadership"},
        )
        hints = self._flatten(gap_map, general)
        assert [h.label for h in hints] == ["Developer Experience"]
        assert hints[0].kind == "claimable"

    def test_keyword_only_entries_are_not_hinted(self):
        """fit_weight 0.0 (pure-ATS keywords) stay out of section hints (parity
        with category_b/c, which exclude them; US204 routes them via the panel)."""
        from applire.services.cv_gap_hints import build_gap_hints

        gap_map, general = build_gap_hints(
            ledger=[_entry("GmbH", status="gap", fit_weight=0.0)],
            category_b=[],
            category_c=[],
            section_contents={"skills": "Leadership"},
        )
        assert self._flatten(gap_map, general) == []

    def test_coverage_is_document_wide_not_per_section(self):
        """A keyword covered in the introduction must not hint on skills."""
        from applire.services.cv_gap_hints import build_gap_hints

        gap_map, general = build_gap_hints(
            ledger=[_entry("Workflow Automation")],
            category_b=["Workflow Automation"],
            category_c=[],
            section_contents={
                "introduction": "I build workflow automation pipelines",
                "skills": "Leadership",
            },
        )
        assert self._flatten(gap_map, general) == []


# ---------------------------------------------------------------------------
# build_gap_hints — legacy fallback (pre-ledger analyses)
# ---------------------------------------------------------------------------

class TestBuildGapHintsLegacy:
    def _flatten(self, gap_map, general):
        return [h for hints in gap_map.values() for h in hints] + list(general)

    def test_legacy_covered_label_is_hidden(self):
        from applire.services.cv_gap_hints import build_gap_hints

        gap_map, general = build_gap_hints(
            ledger=None,
            category_b=["Docker"],
            category_c=["Kubernetes"],
            section_contents={"skills": "Docker\nPython"},
        )
        hints = self._flatten(gap_map, general)
        assert [h.label for h in hints] == ["Kubernetes"]

    def test_legacy_kind_follows_category(self):
        from applire.services.cv_gap_hints import build_gap_hints

        gap_map, general = build_gap_hints(
            ledger=[],  # empty list == no ledger (legacy default)
            category_b=["Docker"],
            category_c=["Kubernetes"],
            section_contents={"skills": "Python"},
        )
        hints = self._flatten(gap_map, general)
        kinds = {h.label: h.kind for h in hints}
        assert kinds == {"Docker": "claimable", "Kubernetes": "honest"}


# ---------------------------------------------------------------------------
# get_cv_sections / patch_cv_section — service level (async, SQLite)
# ---------------------------------------------------------------------------

def _stub_profile_json():
    return {
        "personal_info": {"name": "Test User", "email": "t@example.com"},
        "professional_summary": {"de": "Zusammenfassung", "en": "Summary"},
        "work_experience": [],
        "education": [],
        "skills": [{"name": "Python"}],
        "languages": [],
        "certifications": [],
    }


def _stub_tailored_data():
    return {
        "contact": {"name": "Test User", "email": "t@example.com", "phone": ""},
        "introduction": "Erfahrener Entwickler",
        "work_history": [],
        "education": [],
        "skills": ["Python", "Workflow Automation"],
    }


@pytest_asyncio.fixture
async def db_with_ledger_cv(db):
    from applire.models.user import User
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.gap import GapAnalysis
    from applire.models.cv import GeneratedCV
    from applire.models.flow import FlowSession

    user_id, job_id, profile_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    gap_id, cv_id, flow_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    ledger = [
        _entry("Workflow Automation"),                 # claimable, covered by skills
        _entry("Developer Experience", status="direct"),  # claimable, uncovered
        _entry("DevSecOps", status="gap"),             # honest, uncovered
    ]
    db.add_all([
        User(id=user_id, email="hints@applire.community",
             created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        JobAnalysis(
            id=job_id, raw_text_hash="hints-1", raw_text="job",
            role_title="Dev", required_skills=["Workflow Automation"],
            nice_to_have_skills=[], keywords=[], seniority_level="mid",
            company_culture_signals=[], language_requirement="de",
        ),
        MasterProfile(
            id=profile_id, profile_json=_stub_profile_json(),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        GapAnalysis(
            id=gap_id, job_analysis_id=job_id, profile_id=profile_id,
            match_score=0.8, critical_gaps=[], minor_gaps=[], strengths=[],
            keyword_gaps=[], category_a=[],
            category_b=["Workflow Automation"], category_c=["DevSecOps"],
            keyword_ledger=ledger,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        GeneratedCV(
            id=cv_id, job_analysis_id=job_id, profile_id=profile_id,
            tailored_data=_stub_tailored_data(), template="classic_german",
            status="ready",
            content_snapshot={
                "introduction": "Erfahrener Entwickler",
                "positions": [],
                "skills": ["Python", "Workflow Automation"],
            },
            section_overrides=None,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        ),
        FlowSession(
            id=flow_id, user_id=user_id, job_id=job_id,
            current_step="cv_generation", user_type="new",
            available_actions={}, gap_analysis_id=gap_id,
            generated_cv_id=cv_id,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
    ])
    await db.commit()
    return {"db": db, "cv_id": cv_id, "gap_id": gap_id}


class TestGetCvSectionsLedgerHints:
    @pytest.mark.asyncio
    async def test_covered_claimable_hidden_uncovered_shown_with_kind(self, db_with_ledger_cv):
        from applire.services.cv_section_editor import get_cv_sections

        ctx = db_with_ledger_cv
        result = await get_cv_sections(ctx["cv_id"], ctx["db"])

        all_hints = [g for s in result.sections for g in s.gaps] + list(result.general_gaps)
        labels = {h.label for h in all_hints}
        # covered claimable is hidden (the UAT screenshot bug)
        assert "Workflow Automation" not in labels
        # uncovered claimable + uncovered honest are shown, with kinds
        kinds = {h.label: h.kind for h in all_hints}
        assert kinds.get("Developer Experience") == "claimable"
        assert kinds.get("DevSecOps") == "honest"


class TestPatchDoesNotMutateGapAnalysis:
    @pytest.mark.asyncio
    async def test_patch_leaves_categories_untouched_but_reports_resolved(self, db_with_ledger_cv):
        """Coverage is never persisted into the evidence record (ADR-048 two-axis)."""
        from applire.models.gap import GapAnalysis
        from applire.services.cv_section_editor import patch_cv_section

        ctx = db_with_ledger_cv
        result = await patch_cv_section(
            ctx["cv_id"], "skills",
            "Python\nWorkflow Automation\nDeveloper Experience\nDevSecOps",
            False, ctx["db"],
        )
        # the newly covered, previously-hinted entries are reported as resolved…
        assert "Developer Experience" in result.resolved_gaps
        assert "DevSecOps" in result.resolved_gaps

        # …but the stored gap analysis is NOT rewritten
        gap = await ctx["db"].get(GapAnalysis, ctx["gap_id"])
        await ctx["db"].refresh(gap)
        assert gap.category_b == ["Workflow Automation"]
        assert gap.category_c == ["DevSecOps"]


class TestAssistAcceptsLedgerHints:
    @pytest.mark.asyncio
    async def test_gap_exists_accepts_ledger_concept_not_in_categories(self, db_with_ledger_cv):
        """'Developer Experience' is a direct-status ledger hint — it is not in
        category_b/c, but Kaile assist on its hint chip must still work."""
        from applire.services.cv_assist import _gap_exists

        ctx = db_with_ledger_cv
        assert await _gap_exists(ctx["cv_id"], "Developer Experience", ctx["db"]) is True


# ---------------------------------------------------------------------------
# ATS audit — fourth quadrant: present but unsupported (fabrication warning)
# ---------------------------------------------------------------------------

class TestPresentUnsupported:
    def test_present_honest_gap_keyword_is_flagged(self):
        from applire.services.ats_audit import _keyword_coverage, _norm

        ledger = [_entry("DevSecOps", status="gap")]
        cov = _keyword_coverage(_norm("Skills: DevSecOps, Python"), ["DevSecOps"], ledger)
        assert cov.present == ["DevSecOps"]
        assert cov.present_unsupported == ["DevSecOps"]

    def test_present_claimable_keyword_is_not_flagged(self):
        from applire.services.ats_audit import _keyword_coverage, _norm

        ledger = [_entry("Python", status="direct")]
        cov = _keyword_coverage(_norm("Skills: Python"), ["Python"], ledger)
        assert cov.present == ["Python"]
        assert cov.present_unsupported == []

    def test_no_ledger_flags_nothing(self):
        from applire.services.ats_audit import _keyword_coverage, _norm

        cov = _keyword_coverage(_norm("Skills: DevSecOps"), ["DevSecOps"], None)
        assert cov.present_unsupported == []


class TestSharedPresencePredicate:
    """US212 (#122): gap hints and the ATS panel must judge coverage with the SAME
    instrument — surface_present from ats_audit, morphological fold included."""

    def _flatten(self, gap_map, general):
        return [h for hints in gap_map.values() for h in hints] + list(general)

    def test_plural_surface_form_suppressed_by_singular_document_text(self):
        """#122 'Code reviews' class: document says 'code review standards', the
        entry's surface form is plural — the hint must be suppressed."""
        from applire.services.cv_gap_hints import build_gap_hints

        gap_map, general = build_gap_hints(
            ledger=[_entry("code review practices", surface_forms=["Code reviews"])],
            category_b=["Code reviews"],
            category_c=[],
            section_contents={"experience": "Enforcing code review standards across teams"},
        )
        assert self._flatten(gap_map, general) == []

    def test_covered_delegates_to_ats_audit_surface_present(self):
        """Parity by construction: the hint coverage check resolves to the very same
        function object the ATS audit exports."""
        from applire.services import ats_audit, cv_gap_hints

        assert cv_gap_hints.surface_present is ats_audit.surface_present
