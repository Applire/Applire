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

"""#684 (epic #683) — the professional summary is a positioning seed, not an
interview slot. ADR-061 amended 2026-09-08 (RULING V-0, clauses on
``years_experience`` provenance) + ADR-028 amended (Mode B drops
``professional_summary``). Ground commit: ``dc76cb67``.

Four behaviours pinned here, one section each:

1. ``_apply_set_summary`` is intake-scoped: a Statement intake (interview /
   agent_interview / testimony) DROPS a differing write onto a populated slot
   with a ``not_applied`` receipt; a Document intake (or anything unrecognised
   — the fail-safe direction) keeps the pre-amendment ``Conflict``.
2. The absent-station role gate in ``_apply_upsert_work``: a brand-new,
   dateless work entry from a Statement intake loses its stated role, with
   three carve-outs that must NOT clear it.
3. ``UpsertSkill.years_experience`` is transcribed at the applier, with its own
   coercion validator.
4. The precedence seam shared by two writers (the applier and
   ``skill_enrichment._match_and_enrich``): a transcribed span outranks a
   computed one. Writer A's pin lives in section 3 (the merge-onto-a-computed-
   value test); writer B gets its own dedicated test here.

Plus two smaller pins: the shipped prompt text (section 5) and the ADR-028
Mode B / Mode C split (section 6).
"""
from __future__ import annotations

import pytest

from applire.prompts.interview import _SECTION_GUIDANCE, _SECTION_LABELS
from applire.prompts.reconcile import RECONCILE_SYSTEM_PROMPT
from applire.schemas.profile import MasterProfileData, ProfessionalSummary, Skill, WorkEntry
from applire.services.profile.completeness import field_gaps
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.ops import SetSummary, UpsertSkill, UpsertWork
from applire.services.skill_enrichment import enrich_skills_deterministic

# The three Statement intakes (ADR-063 §5.3.19a's INTAKE axis) vs. one
# Document intake used throughout as the contrasting case.
STATEMENT_SOURCE = "interview"
DOCUMENT_SOURCE = "cv_upload"


# ── 1. _apply_set_summary is intake-scoped ─────────────────────────────────────


def test_a_statement_intake_drops_a_differing_summary_and_receipts_it():
    """ADR-061 amended 2026-09-08 (#684) — the candidate is not asked to choose
    between their own self-description and their own interview answer. A
    ``set_summary`` from a Statement source against an already-populated slot
    is dropped: no conflict, one ``not_applied`` receipt
    (``reason="summary_populated"``), and the stored text is untouched.
    """
    profile = MasterProfileData(
        professional_summary=ProfessionalSummary(de="Erfahrener Entwickler mit 10 Jahren.")
    )
    ops = [SetSummary(lang="de", text="15 Jahre Erfahrung in der pharmazeutischen Fertigung.")]

    result = apply_ops(profile, ops, STATEMENT_SOURCE)

    assert result.profile.professional_summary.de == "Erfahrener Entwickler mit 10 Jahren."
    assert result.conflicts == []
    assert len(result.not_applied) == 1
    receipt = result.not_applied[0]
    assert receipt.section == "professional_summary"
    assert receipt.label == "de"
    assert receipt.reason == "summary_populated"
    assert not [c for c in result.changes if c.section == "professional_summary"]


def test_a_document_intake_still_disputes_a_differing_summary():
    """The paragraph #113(b) originally wrote is unchanged: two CVs disagreeing
    about the candidate's self-description is a real either/or, so a Document
    intake against a populated slot still raises a ``Conflict`` and leaves
    ``not_applied`` empty — the two receipt channels never fire for the same
    write.
    """
    profile = MasterProfileData(
        professional_summary=ProfessionalSummary(de="Erfahrener Entwickler mit 10 Jahren.")
    )
    ops = [SetSummary(lang="de", text="Senior Engineer, Plattformteams.")]

    result = apply_ops(profile, ops, DOCUMENT_SOURCE)

    assert result.profile.professional_summary.de == "Erfahrener Entwickler mit 10 Jahren."
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict.section == "professional_summary"
    assert conflict.field == "de"
    assert result.not_applied == []


@pytest.mark.parametrize("source", [STATEMENT_SOURCE, DOCUMENT_SOURCE])
def test_an_empty_summary_slot_is_filled_regardless_of_intake(source):
    """The seed case: an empty slot is not a dispute under either intake class
    — both write the text, with a ``FieldChange`` and no conflict/receipt. The
    intake-scoping in section 1 only fires once the slot is already populated.
    """
    profile = MasterProfileData()
    ops = [SetSummary(lang="de", text="Neuer Text.")]

    result = apply_ops(profile, ops, source)

    assert result.profile.professional_summary.de == "Neuer Text."
    assert result.conflicts == []
    assert result.not_applied == []
    assert any(
        c.section == "professional_summary" and c.action == "added" for c in result.changes
    )


@pytest.mark.parametrize("source", [STATEMENT_SOURCE, DOCUMENT_SOURCE])
def test_a_restatement_of_the_stored_summary_is_a_no_op_regardless_of_intake(source):
    """A normalised-equal restatement was already a no-op before this
    amendment (``_norm(old) == _norm(op.text)``) and stays one under both
    intake classes — it must never be counted as a drop (no receipt) or a
    dispute (no conflict).
    """
    profile = MasterProfileData(professional_summary=ProfessionalSummary(de="Erfahrener Entwickler."))
    ops = [SetSummary(lang="de", text="  Erfahrener Entwickler.  ")]

    result = apply_ops(profile, ops, source)

    assert result.profile.professional_summary.de == "Erfahrener Entwickler."
    assert result.conflicts == []
    assert result.not_applied == []
    assert not [c for c in result.changes if c.section == "professional_summary"]


def test_an_unrecognised_source_falls_back_to_the_document_dispute():
    """The deliberate fail-safe direction named in ``_STATEMENT_SOURCES``'s own
    docstring: a source outside the three-member Statement set (here,
    ``"migration"``) is treated as a Document intake — the pre-amendment
    ``Conflict``, not a silent drop — because an unrecognised intake must never
    default to dropping a value.
    """
    profile = MasterProfileData(professional_summary=ProfessionalSummary(de="Erfahrener Entwickler."))
    ops = [SetSummary(lang="de", text="Ganz andere Fassung.")]

    result = apply_ops(profile, ops, "migration")

    assert result.profile.professional_summary.de == "Erfahrener Entwickler."
    assert len(result.conflicts) == 1
    assert result.conflicts[0].section == "professional_summary"
    assert result.conflicts[0].field == "de"
    assert result.not_applied == []


# ── 2. The absent-station role gate in _apply_upsert_work ──────────────────────

_STATION = "Novandis Pharma GmbH"
_STATED_ROLE = "Manufacturing IT Lead"


def test_a_new_dateless_statement_work_entry_loses_its_stated_role():
    """ADR-061 amended 2026-09-08 (#684, RULING V-0) — a brand-new work entry
    from a Statement intake, mentioned with no dates at all, is created with
    its role cleared: deterministic code cannot tell a stated title from an
    invented one, but "no dates given" IS a fact it can read off the op.
    """
    profile = MasterProfileData()
    ops = [UpsertWork(ref="w1", company=_STATION, role=_STATED_ROLE)]

    result = apply_ops(profile, ops, STATEMENT_SOURCE)

    assert len(result.profile.work_experience) == 1
    entry = result.profile.work_experience[0]
    assert entry.company == _STATION
    assert entry.role == ""


def test_a_dated_new_statement_work_entry_keeps_its_stated_role():
    """Carve-out (a): the gate names its shape as "no dates at all" — a station
    described fully enough to date is a different shape and keeps its role,
    even from the same Statement intake.
    """
    profile = MasterProfileData()
    ops = [UpsertWork(ref="w1", company=_STATION, role=_STATED_ROLE, start_date="2020-01")]

    result = apply_ops(profile, ops, STATEMENT_SOURCE)

    assert result.profile.work_experience[0].role == _STATED_ROLE


def test_a_dateless_new_document_work_entry_keeps_its_stated_role():
    """Carve-out (b): the gate is scoped to Statement intakes only — a CV/
    LinkedIn import (Document intake) never composes a plausible title in the
    first place, so a dateless new entry from one keeps whatever role the
    document stated.
    """
    profile = MasterProfileData()
    ops = [UpsertWork(ref="w1", company=_STATION, role=_STATED_ROLE)]

    result = apply_ops(profile, ops, DOCUMENT_SOURCE)

    assert result.profile.work_experience[0].role == _STATED_ROLE


def test_a_merge_onto_an_existing_entry_still_routes_a_differing_role_to_aliases():
    """Carve-out (c): the gate only fires on the "create a NEW entry" branch
    (``target is None``). A merge — ``op.target`` naming an existing entry —
    never reaches the gate at all, so a differing role keeps its pre-existing
    ADR-013 Rule 1 behaviour: it becomes a role alias, and the entry's own
    ``role`` is never overwritten (let alone cleared), regardless of intake or
    missing dates.
    """
    existing = WorkEntry(company=_STATION, role="Support Engineer")
    profile = MasterProfileData(work_experience=[existing])
    ops = [UpsertWork(ref="w1", target=existing.id, company=_STATION, role="Senior Engineer")]

    result = apply_ops(profile, ops, STATEMENT_SOURCE)

    entry = result.profile.work_experience[0]
    assert entry.role == "Support Engineer"
    assert "Senior Engineer" in entry.role_aliases


# ── 3. UpsertSkill.years_experience is transcribed ──────────────────────────────


def test_a_new_skill_carrying_a_stated_span_is_written_as_transcribed():
    """A brand-new skill (no near-dupe to merge into) whose op states a span
    writes both the number and its provenance in one place — the two must
    never separate, or a downstream reader would trust an untranscribed
    number.
    """
    profile = MasterProfileData()
    ops = [UpsertSkill(name="GMP-regulated manufacturing", years_experience=15)]

    result = apply_ops(profile, ops, STATEMENT_SOURCE)

    assert len(result.profile.skills) == 1
    skill = result.profile.skills[0]
    assert skill.years_experience == 15
    assert skill.source == "transcribed"


def test_a_merged_stated_span_overwrites_an_existing_computed_one():
    """Property 3 ("a MERGE onto an existing skill also sets both") AND the
    writer-A instance of property 4 (the precedence seam): the existing skill
    already carries a ``computed`` span from prior enrichment, and the
    transcribed span from this op WINS — deliberately overwriting a non-null
    value, because clause 5's ceiling logic reads "where the candidate speaks,
    they win" even when code had already written something there.
    """
    existing = Skill(name="Python", years_experience=6, source="computed")
    profile = MasterProfileData(skills=[existing])
    ops = [UpsertSkill(name="Python", years_experience=15)]

    result = apply_ops(profile, ops, STATEMENT_SOURCE)

    assert len(result.profile.skills) == 1
    skill = result.profile.skills[0]
    assert skill.years_experience == 15
    assert skill.source == "transcribed"


def test_an_absent_span_on_the_op_never_touches_an_existing_skills_span():
    """The fail-safe direction of ``_apply_transcribed_years``: an op that
    states no span (``years_experience=None``) must leave whatever the skill
    already carried — number AND provenance label — completely alone.
    """
    existing = Skill(name="Python", years_experience=6, source="computed")
    profile = MasterProfileData(skills=[existing])
    ops = [UpsertSkill(name="Python", years_experience=None)]

    result = apply_ops(profile, ops, STATEMENT_SOURCE)

    skill = result.profile.skills[0]
    assert skill.years_experience == 6
    assert skill.source == "computed"


def test_years_experience_coercion_accepts_a_stated_span_and_refuses_the_rest():
    """``UpsertSkill._coerce_years`` — a model asked for a number will
    sometimes render the candidate's own "15+" verbatim, or a bare float; both
    must coerce to a clean int. An uncoercible value ("many") must fall to
    ``None`` on that ONE field, never invalidate the whole op — the direction
    every guard in this module fails in.
    """
    assert UpsertSkill(name="X", years_experience="15+").years_experience == 15
    assert UpsertSkill(name="X", years_experience=15.0).years_experience == 15

    op = UpsertSkill(name="X", years_experience="many")
    assert op.years_experience is None
    assert op.name == "X"  # the rest of the op parsed cleanly


# ── 4. The precedence seam, writer B: skill_enrichment._match_and_enrich ───────


def test_deterministic_enrichment_leaves_a_transcribed_span_alone_but_still_computes_its_sibling():
    """ADR-061 amended 2026-09-08 (#684) — writer B's half of the precedence
    seam. A skill already carrying ``source="transcribed"`` / ``years_experience
    =15`` whose name IS demonstrated by a dated work entry (``matched_ranges``
    non-empty, so the OLD code's ``_COMPUTED`` branch would have fired and
    overwritten it) keeps its stated span verbatim through
    ``enrich_skills_deterministic`` — only ``experience_refs`` gains the
    matched org label, because that is evidence, not the span.

    A sibling skill on the SAME profile with no transcribed span still gets
    the code-computed duration: the branch introduced by this amendment is
    narrow (gated on ``source == "transcribed" and years_experience is not
    None``), not a blanket skip of the deterministic pass.
    """
    stated = Skill(
        name="GMP-regulated manufacturing",
        category="domain",
        years_experience=15,
        source="transcribed",
    )
    computed = Skill(name="SAP", category="technical")
    profile = MasterProfileData(
        skills=[stated, computed],
        work_experience=[
            WorkEntry(
                company=_STATION,
                role=_STATED_ROLE,
                start_date="2010-01",
                end_date="2015-01",
                technologies=["SAP"],
                responsibilities=["Led GMP-regulated manufacturing IT projects"],
            )
        ],
    )

    enriched = enrich_skills_deterministic(profile)

    stated_out = next(s for s in enriched.skills if s.name == "GMP-regulated manufacturing")
    assert stated_out.years_experience == 15
    assert stated_out.source == "transcribed"
    assert _STATION in stated_out.experience_refs

    computed_out = next(s for s in enriched.skills if s.name == "SAP")
    assert computed_out.years_experience == 5
    assert computed_out.source == "computed"


# ── 5. The shipped prompt text ──────────────────────────────────────────────────


def test_the_reconcile_prompt_carries_the_four_summary_positioning_rules():
    """Category B (applire-prompt-first skill) — the teach half of this fix.
    Each assertion pins a short, distinctive substring from its own
    operation's paragraph in ``RECONCILE_SYSTEM_PROMPT``, not a whole
    paragraph, so an unrelated wording tweak elsewhere in the prompt cannot
    quietly redden this test.
    """
    assert "role is OPTIONAL" in RECONCILE_SYSTEM_PROMPT  # upsert_work
    assert "ONLY the clause about ITS OWN" in RECONCILE_SYSTEM_PROMPT  # add_bullets
    assert "years_experience" in RECONCILE_SYSTEM_PROMPT  # upsert_skill
    assert (
        "ONLY a span the new information itself STATES" in RECONCILE_SYSTEM_PROMPT
    )  # upsert_skill
    assert (
        "Use ONLY when the slot for that language is EMPTY" in RECONCILE_SYSTEM_PROMPT
    )  # set_summary, gap-only wording


# ── 6. ADR-028: Mode B drops professional_summary, Mode C keeps it ─────────────


def test_mode_b_drops_professional_summary_while_mode_c_keeps_it():
    """ADR-028 amended 2026-09-08 (#683) — the job-driven interview (Mode B)
    stops soliciting free prose into the write-once summary slot; the
    profile-enrichment interview (Mode C) is untouched. This is the whole
    scope of that amendment, so one test covers both directions:

    * ``_MODE_B_CORE_SECTIONS`` no longer names ``professional_summary``.
    * Mode C's guided-question copy (``prompts/interview.py``) still carries a
      ``professional_summary`` entry in both its label and guidance maps.
    * ``completeness.field_gaps`` (Mode C's gap source) still emits the
      ``professional_summary`` gap for a profile that has work experience and
      an empty summary.
    """
    from applire.services.interview_graph import _MODE_B_CORE_SECTIONS

    assert "professional_summary" not in _MODE_B_CORE_SECTIONS

    assert "professional_summary" in _SECTION_LABELS
    assert "professional_summary" in _SECTION_GUIDANCE

    profile = {
        "work_experience": [{"company": "Acme", "role": "Dev", "achievements": ["Shipped X"]}],
        "professional_summary": "",
    }
    assert "professional_summary" in field_gaps(profile)
