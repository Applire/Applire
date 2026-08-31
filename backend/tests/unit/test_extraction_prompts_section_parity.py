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

import re

from applire.prompts import cv_extraction, cv_extraction_segmented, profile_extraction
from applire.schemas.profile import OBJECT_SECTIONS, VAULT_SECTIONS

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
