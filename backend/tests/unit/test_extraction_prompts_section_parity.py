# Copyright (C) 2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify it under the
# terms of the GNU Affero General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License for more
# details.
#
# You should have received a copy of the GNU Affero General Public License along
# with this program. If not, see <https://www.gnu.org/licenses/>.
"""#619 regression guard — every extraction door offers every vault LIST section.

There are THREE CV/profile extraction prompts, reached by different channels:

  * ``cv_extraction``           — the browser ``/upload`` and import-job path (SPLIT).
  * ``cv_extraction_segmented`` — the ADR-047 fallback for a CV too dense for one call
    (outline → per-role detail → core passes).
  * ``profile_extraction``      — the ``import_from_text`` / ``import_from_pdf`` path,
    i.e. the MCP/agent ``import_cv`` tool, LinkedIn export import, and paste-text
    import (FLAT).

This is the THIRD time a section/field has existed in ``cv_extraction`` (and, since
ADR-047, its segmented sibling) while being structurally unaskable on the
``profile_extraction`` door — not a model failure (applire-prompt-first Category A),
because a field absent from a prompt's JSON schema cannot be emitted no matter how
good the model is:

  * #190 (2026-07-17) — certifications. Fixed on ``cv_extraction`` only; the
    ``profile_extraction`` door kept dropping certifications until a second pass;
    see ``test_extraction_prompts_certifications.py``.
  * #229 (2026-07-29) — achievements/responsibilities/technologies. Same shape; see
    ``test_extraction_prompts_achievements.py``.
  * #619 (2026-08-30) — projects/publications/volunteer_activities. This file.

Each prior fix was a bespoke, hand-picked pair of assertions for the ONE field that
had just been reported missing. That pattern guarantees a fourth repetition on the
next new section, because nothing ever compared the doors' full section sets — only
ever the one field someone had just noticed. This test compares the full set instead,
mechanically, so a future addition to one door's schema that is not mirrored on the
others fails HERE instead of shipping silently.

Scope — LIST sections only. ``schemas.profile.VAULT_SECTIONS`` also carries two
OBJECT-shaped sections (``personal_info``, ``professional_summary`` — merge-patch
semantics, ``schemas.profile.OBJECT_SECTIONS``) and one reconciler-only section
(``signature_stories`` — ADR-055, written by ``upsert_story`` from interview
evidence, never emitted by any extraction prompt by design). Both are excluded
below; the eight LIST sections are exactly the shape #190/#229/#619 concern.

OBJECT sections are guarded too, one test below. They were nearly scoped out as
"not what #619 reported" — but the System-FMEA row this defect belongs to
(SF-PROFILE.8) names the loss as *"a CV's Projects section (and its summary
paragraph) never reaches the vault"*. The summary paragraph IS the OBJECT-shaped
``professional_summary``, which the FLAT door was also missing; scoping against the
issue text alone would have left the row's own wording uncredited. Both shapes are
asserted here.

Why prompt-content assertions (not a mock-based import test): ``MockLLMProvider``
returns one canned dict (``_PROFILE_PARSE_RESPONSE``) for BOTH the SPLIT and FLAT
doors' "cv analyst" prompts — it already contains "projects", regardless of what
either prompt asks for — so a mock-provider import test cannot see this defect
either way. Only reading the prompt text catches it (same reasoning as the two
precedent files).
"""

import json
import re

from applire.prompts import cv_extraction, cv_extraction_segmented, profile_extraction
from applire.schemas.profile import (
    Certification,
    EducationEntry,
    Language,
    OBJECT_SECTIONS,
    PersonalInfo,
    ProfessionalSummary,
    ProjectEntry,
    Publication,
    Skill,
    VAULT_SECTIONS,
    VolunteerActivity,
    WorkEntry,
)

# Every extraction prompt's schema is pretty-printed with the top-level section keys
# at exactly one indent level (2 spaces) and every nested field key at a deeper one —
# true of cv_extraction.py's schema (genuinely valid JSON), cv_extraction_segmented.py's
# compact non-JSON set-literal shorthand (ADR-047 keeps per-call prompts small), and
# profile_extraction.py's schema alike. Anchoring on that indentation, rather than on
# `"\w+":` anywhere, is what keeps this a SECTION-name extractor instead of also
# catching every nested field name (which would make every door "different" by
# construction, since none of the three nests its fields identically).
def _top_level_keys(schema_text: str) -> set[str]:
    return set(re.findall(r'(?m)^  "(\w+)":', schema_text))


# profile_extraction.py predates ADR-044's experience unification and kept its own
# key name for the one LIST section whose name differs; MasterProfileData migrates
# it back (schemas/profile.py::_migrate_legacy_fields). Not a gap — the SAME concept
# under a different, already-handled name.
_LEGACY_NAME_ALIASES = {"work_history": "work_experience", "contact": "personal_info"}


def _canonicalize(section_names: set[str]) -> set[str]:
    return {_LEGACY_NAME_ALIASES.get(s, s) for s in section_names}


# The ground truth of what a vault MAY hold, minus the two OBJECT-shaped sections and
# the one reconciler-only section — see module docstring "Scope".
LIST_SECTIONS: frozenset[str] = VAULT_SECTIONS - OBJECT_SECTIONS - {"signature_stories"}


def test_list_sections_is_not_accidentally_empty():
    """Guards the guard: every assertion below is vacuous if this is wrong.

    Pinned to the concrete set (not just "non-empty") so a change to VAULT_SECTIONS
    is a deliberate, visible edit here too — not a silent widening or narrowing of
    what this file protects.
    """
    assert LIST_SECTIONS == {
        "work_experience",
        "education",
        "certifications",
        "skills",
        "languages",
        "publications",
        "volunteer_activities",
        "projects",
    }


def test_cv_extraction_door_offers_every_list_section():
    """The browser /upload door — guard the reference door from regressing too."""
    have = _top_level_keys(cv_extraction._SCHEMA_DESCRIPTION)
    missing = LIST_SECTIONS - have
    assert not missing, f"cv_extraction.py is missing sections: {missing}"


def test_segmented_door_offers_every_list_section():
    """ADR-047 fallback — outline pass carries work_experience, core carries the rest."""
    have = _top_level_keys(cv_extraction_segmented.EXTRACTION_OUTLINE_SYSTEM_PROMPT)
    have |= _top_level_keys(cv_extraction_segmented.EXTRACTION_CORE_SYSTEM_PROMPT)
    missing = LIST_SECTIONS - have
    assert not missing, f"cv_extraction_segmented.py is missing sections: {missing}"


def test_profile_extraction_door_offers_every_list_section():
    """#619: the MCP import_cv / LinkedIn / paste-text door (FLAT schema).

    THE standing guard this file exists for: add a section to cv_extraction.py
    without mirroring it here, and this goes red instead of becoming the fourth
    silent repetition of the #190/#229/#619 class.
    """
    have = _canonicalize(_top_level_keys(profile_extraction.SYSTEM_PROMPT))
    missing = LIST_SECTIONS - have
    assert not missing, (
        f"profile_extraction.py SYSTEM_PROMPT is missing sections: {missing} — "
        "the model cannot emit a field it was never given (applire-prompt-first "
        "Category A), see #190/#229/#619"
    )


def test_every_door_offers_every_object_section():
    """SF-PROFILE.8 covers the summary paragraph as well as Projects.

    ``professional_summary`` and ``personal_info`` are merge-patch OBJECT sections
    (``schemas.profile.OBJECT_SECTIONS``), not lists — a different shape, the same
    failure mode: a key absent from a door's schema cannot be emitted through it.
    The FLAT door carried neither ``professional_summary`` (added with #619) nor the
    name ``personal_info`` (it calls it ``contact``, aliased above like
    ``work_history``). Guarding both shapes is what makes this file the whole gate
    rather than the list half of one.
    """
    doors = {
        "cv_extraction.py": _top_level_keys(cv_extraction._SCHEMA_DESCRIPTION),
        "cv_extraction_segmented.py": (
            _top_level_keys(cv_extraction_segmented.EXTRACTION_OUTLINE_SYSTEM_PROMPT)
            | _top_level_keys(cv_extraction_segmented.EXTRACTION_CORE_SYSTEM_PROMPT)
        ),
        "profile_extraction.py": _top_level_keys(profile_extraction.SYSTEM_PROMPT),
    }
    for door, have in doors.items():
        missing = OBJECT_SECTIONS - _canonicalize(have)
        assert not missing, f"{door} is missing OBJECT sections: {missing}"


def test_profile_extraction_projects_block_carries_associated_experience():
    """Field-level, not just section-level: associated_experience is what lets the
    reconciler (reconcile/apply.py::_apply_upsert_project) and the initial-import
    consumers (services/cv.py, services/oracle/extract.py, services/cv_budget.py,
    services/choice_grounding.py, services/letter_figure_guard.py,
    services/oracle/matchers/vault.py — all already id-OR-name dual-aware, ADR-046)
    nest a project under its parent job, exactly as the cv_extraction.py door
    already lets them. A "projects" key with no associated_experience field would
    pass the section-presence test above while still losing the parent link.
    """
    assert '"associated_experience"' in profile_extraction.SYSTEM_PROMPT


def test_profile_extraction_has_projects_no_folding_rule():
    """Mirrors cv_extraction.py's PROJECTS rule: without it, a model asked for both
    work_history and projects has no instruction against describing the same
    project as a work_history bullet instead of a projects entry."""
    system = profile_extraction.SYSTEM_PROMPT
    assert "PROJECTS" in system
    assert "folded into work_history" in system


# ─── #228 — FIELD-level parity (the section-level gate above cannot see this) ──
#
# A section-level PASS ("profile_extraction.py offers `skills`") is compatible
# with the model being asked for none of that section's actual substructure:
# before this fix, profile_extraction.py's "skills" schema was a bare
# ["list of technical and soft skills"] — a real `skills` key, offering ZERO
# of Skill's five extraction-relevant fields. #190/#229/#619 were "a whole
# SECTION is structurally unaskable"; #228 is the identical failure mode one
# level deeper — a FIELD unaskable inside a section both doors already claim
# to offer. Same fix shape applies here: derive the expected set mechanically
# (each section's own Pydantic model field list) rather than hand-typing the
# fields someone just noticed missing, so the next field added to
# cv_extraction.py and forgotten elsewhere fails HERE instead of shipping.
#
# THE DIFF IS SCOPED PER SECTION, NOT FLAT. A field name reused across two
# unrelated sections (`location` names both PersonalInfo's home city and
# WorkEntry's per-role office city) must be checked against ITS OWN section's
# model, never against "does this field name appear ANYWHERE in the schema".
# A flat/global diff would call `work_experience.location` satisfied merely
# because `personal_info.location` shares the name — exactly the false-clear
# this scoping prevents. (#228's own measurement note: the issue's
# hand-collected 17-field list undercounted by exactly one field for this
# reason — work_history.location — while separately over-counting
# `linkedin_url` as fully absent when a legacy alias already carried its
# value under the name `linkedin`; see the field allowlist/rename discussion
# in the PR that added this block.)

#: section -> the Pydantic model whose fields an extraction prompt may ask for.
_SECTION_MODEL: dict[str, type] = {
    "personal_info": PersonalInfo,
    "professional_summary": ProfessionalSummary,
    "work_experience": WorkEntry,
    "education": EducationEntry,
    "certifications": Certification,
    "skills": Skill,
    "languages": Language,
    "publications": Publication,
    "volunteer_activities": VolunteerActivity,
    "projects": ProjectEntry,
}

# Guards the guard: every section this file's LIST_SECTIONS/OBJECT_SECTIONS
# cover has a model above (signature_stories excluded — reconciler-only, see
# the module docstring's Scope paragraph; never emitted by any extraction
# prompt by design, so no door is asked to carry its fields either).
assert set(_SECTION_MODEL) == VAULT_SECTIONS - {"signature_stories"}


#: Fields that exist on a section's Pydantic model but are NEVER an
#: extraction target — minted, computed, or written later by a specific
#: piece of code, never something a CV-extraction LLM is asked to fill in.
#: Each entry names the mechanism that actually populates it, so "why isn't
#: this in the schema" has a one-line answer instead of an implicit
#: assumption. Keyed by field name; value is the set of sections it is
#: internal FOR (None = every section it appears in).
_INTERNAL_FIELDS: dict[str, frozenset[str] | None] = {
    "id": None,  # every entity — uuid4 default_factory, never LLM-supplied
    "status": frozenset({"certifications", "skills", "languages"}),  # ADR-061 cl.3 — reconcile/stance.py
    "expected_fields": frozenset({"work_experience", "volunteer_activities", "projects"}),  # ADR-041 — services/profile/expectations.py
    "role_fact_projections": frozenset({"work_experience"}),  # #328 opt.4 — services/profile/role_facts.py
    "experience_refs": frozenset({"skills"}),  # provenance — reconcile/apply.py evidence resolution
    "source": frozenset({"skills"}),  # years_experience provenance — services/skill_enrichment.py
    "photo_url": frozenset({"personal_info"}),  # photo UPLOAD feature, not CV/text extraction — no door asks for it
}


def _is_internal(section: str, field: str) -> bool:
    if field not in _INTERNAL_FIELDS:
        return False
    scope = _INTERNAL_FIELDS[field]
    return scope is None or section in scope


def _expected_fields(section: str) -> set[str]:
    """The extraction-askable field set for one section, straight off its
    Pydantic model — never hand-typed. This IS the #228 fix: a field added to
    schemas/profile.py and forgotten by every prompt now has nothing to hide
    behind."""
    model = _SECTION_MODEL[section]
    return {f for f in model.model_fields if not _is_internal(section, f)}


#: (section, field) pairs NO door currently asks for — EVEN cv_extraction.py.
#: Pre-existing, symmetric across all three doors, and out of #228's scope
#: (#228 is "profile_extraction.py lacks what cv_extraction.py already has";
#: none of these three is a case of that — cv_extraction.py itself lacks
#: them too, by this same measurement). Each entry needs its own reason: an
#: unexplained exemption is how the next gap hides.
_FIELD_ALLOWLIST: dict[tuple[str, str], str] = {
    ("volunteer_activities", "is_current"): (
        "VolunteerActivity inherits is_current from ExperienceBase, but no door "
        "— including cv_extraction.py — has ever asked for an ongoing/ended "
        "marker on a volunteer role; only work_experience carries one. "
        "Pre-existing and symmetric across all three doors, not a #228 gap."
    ),
    ("projects", "is_current"): (
        "ProjectEntry inherits is_current from ExperienceBase; no door asks for "
        "an ongoing/ended marker on a project (its start_date/end_date already "
        "carry the span). Pre-existing and symmetric, not a #228 gap."
    ),
    ("projects", "location"): (
        "ProjectEntry inherits location from ExperienceBase; no door asks for a "
        "project's own location (associated_experience already ties it to the "
        "work/volunteer entry whose location applies). Pre-existing and "
        "symmetric, not a #228 gap."
    ),
}


def _json_obj_field_sections(obj: dict) -> dict[str, set[str]]:
    """One level of a parsed schema dict -> {key: {its own nested field names}}.

    A dict value's own keys are the fields; a list-of-object value's first
    element's keys are the fields (every list section here is homogeneous); a
    scalar/string leaf (e.g. profile_extraction.py's old ``"skills": ["..."]``
    shorthand) carries no field names at all — an empty set, not an error, so
    a shape mismatch like that shows up as EVERY field of that section
    missing, not a crash."""
    out: dict[str, set[str]] = {}
    for key, value in obj.items():
        if isinstance(value, dict):
            out[key] = set(value.keys())
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            out[key] = set(value[0].keys())
        else:
            out[key] = set()
    return out


def _shorthand_field_sections(block: str) -> dict[str, set[str]]:
    """cv_extraction_segmented.py's CORE pass uses a compact NON-JSON
    set-literal shorthand (module docstring; see also
    test_segmented_door_offers_every_list_section above) —
    '"section": {"f1","f2"}' rather than real JSON. Anchor on the same
    2-space top-level indent _top_level_keys() uses, slice each section's own
    text span, then pull quoted field tokens out of that span.

    `"(\\w+)"` cannot match a description string containing a space or a `|`
    (every description in this block has one or the other, e.g. "German
    summary or null", "technical|soft|language|domain"), so it never mistakes
    a description for a field name — checked against this exact block before
    this test was written (a throwaway measurement script, not committed)."""
    starts = [(m.group(1), m.start()) for m in re.finditer(r'(?m)^  "(\w+)":', block)]
    starts.append((None, len(block)))
    out: dict[str, set[str]] = {}
    for i, (name, start) in enumerate(starts[:-1]):
        span = block[start:starts[i + 1][1]]
        after_colon = span.split(":", 1)[1] if ":" in span else span
        out[name] = set(re.findall(r'"(\w+)"', after_colon))
    return out


def _cv_extraction_fields() -> dict[str, set[str]]:
    """The rich door — _SCHEMA_DESCRIPTION is valid JSON on its own (every
    value is a description STRING, never a real type, but the STRUCTURE
    parses), so real json.loads gives an exact nested field map — no regex
    heuristic needed."""
    return _json_obj_field_sections(json.loads(cv_extraction._SCHEMA_DESCRIPTION))


def _profile_extraction_fields() -> dict[str, set[str]]:
    """The flat door — its JSON schema block follows a literal 'Schema:\\n'
    marker and is itself valid JSON, same reasoning as cv_extraction.py."""
    schema_text = profile_extraction.SYSTEM_PROMPT.split("Schema:\n", 1)[1]
    fields = _json_obj_field_sections(json.loads(schema_text))
    # profile_extraction.py predates ADR-044's section rename (handled by
    # _LEGACY_NAME_ALIASES above) — canonicalize the SECTION name here too,
    # field level. Unlike the section-level alias, there is no per-FIELD
    # alias: post-#228 this door asks for "linkedin_url" verbatim (renamed
    # from the legacy "linkedin"), matching cv_extraction.py's field NAME as
    # well as its section — issue #228 instruction 1 ("field-for-field
    # identical in name and shape"), so a future regression back to the old
    # name must fail here, not be quietly re-aliased away.
    if "work_history" in fields:
        fields["work_experience"] = fields.pop("work_history")
    if "contact" in fields:
        fields["personal_info"] = fields.pop("contact")
    return fields


def _segmented_fields() -> dict[str, set[str]]:
    """The ADR-047 fallback door — the outline pass is real JSON; the core
    pass is the compact shorthand (see _shorthand_field_sections). The DETAIL
    pass's fields (responsibilities/achievements/technologies) belong to
    work_experience too — together with OUTLINE they are what one segmented
    work entry carries (EXTRACTION_DETAIL_SYSTEM_PROMPT's own module comment:
    "outline-then-expand")."""
    outline_schema = (
        cv_extraction_segmented.EXTRACTION_OUTLINE_SYSTEM_PROMPT
        .split("Return:\n", 1)[1].split("\n\nRules:")[0]
    )
    outline = _json_obj_field_sections(json.loads(outline_schema))

    core_schema = (
        cv_extraction_segmented.EXTRACTION_CORE_SYSTEM_PROMPT
        .split("omit a key):\n", 1)[1].split("\n\nRules:")[0]
    )
    core = _shorthand_field_sections(core_schema)

    detail_schema = (
        cv_extraction_segmented.EXTRACTION_DETAIL_SYSTEM_PROMPT
        .split("Return:\n", 1)[1].split("\n\nRules:")[0]
    )
    detail_fields = set(json.loads(detail_schema).keys())

    merged = dict(core)
    merged["work_experience"] = outline.get("work_experience", set()) | detail_fields
    return merged


def _assert_field_parity(door_name: str, have: dict[str, set[str]]) -> None:
    failures = []
    for section in _SECTION_MODEL:
        missing = _expected_fields(section) - have.get(section, set())
        missing = {f for f in missing if (section, f) not in _FIELD_ALLOWLIST}
        if missing:
            failures.append(f"{section}: {sorted(missing)}")
    assert not failures, (
        f"{door_name} is missing fields schemas/profile.py declares "
        f"extraction-askable (not in _INTERNAL_FIELDS or _FIELD_ALLOWLIST): "
        + "; ".join(failures)
    )


def test_field_allowlist_is_not_accidentally_empty():
    """Guards the guard, same reasoning as test_list_sections_is_not_
    accidentally_empty above: every entry needs its own reason (see the
    dict's own comments), so a change here must be a deliberate, visible
    edit — never a silent widening that hides the next field-level gap."""
    assert set(_FIELD_ALLOWLIST) == {
        ("volunteer_activities", "is_current"),
        ("projects", "is_current"),
        ("projects", "location"),
    }
    assert all(reason.strip() for reason in _FIELD_ALLOWLIST.values())


def test_cv_extraction_door_field_parity():
    """The reference door — guarded too, so _FIELD_ALLOWLIST (built by running
    this SAME diff against cv_extraction.py) cannot silently widen without a
    test catching it."""
    _assert_field_parity("cv_extraction.py", _cv_extraction_fields())


def test_segmented_door_field_parity():
    _assert_field_parity("cv_extraction_segmented.py", _segmented_fields())


def test_profile_extraction_door_field_parity():
    """#228: THE standing guard this block exists for. profile_extraction.py
    reached section-level parity in #619 (test_profile_extraction_door_offers_
    every_list_section above) while still being field-SHALLOW inside those
    sections: 18 fields missing across personal_info/work_history/education/
    skills (measured 2026-09-01 — one more than the issue's hand-collected 17;
    see the module comment above this block), plus `skills` itself being
    list[str] rather than list[object], so it offered NONE of Skill's fields,
    not merely some of them. Add a field to cv_extraction.py's schema without
    mirroring it here (or allowlisting it with a reason) and this goes red
    instead of becoming the fourth silent repetition of the
    #190/#229/#619/#228 class."""
    _assert_field_parity("profile_extraction.py", _profile_extraction_fields())
