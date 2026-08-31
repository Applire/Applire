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

"""Skills-list SEMANTIC duplicates (ADR-062, applire-prompt-first, 2026-08-31).

Ground truth: the 2026-08-30 delivered CV carried 25 skills-list entries, and a blind
fachbereich reviewer named the redundancy unprompted. ``ats_audit.skills_page_dupe``
(the deterministic page-duplicate predicate ``_dedup_skills`` / ``_tailor_skills_to_jd``
/ ``_drop_ungrounded_jd_echo_skills`` all share) correctly collapsed the containment-
shaped pairs in that list -- ``Feinplanung/Fertigungssteuerung``/``Fertigungssteuerung``,
``KVP``/``KVP/Kaizen``, ``SAP``/``SAP PP`` -- and, BY DESIGN, cannot reach four more:

    MES                    <-> Maschinen- und Betriebsdatenerfassung  (acronym / description)
    Lean Management        <-> Lean Production                        (synonym)
    Budgetverantwortung    <-> Budgetplanung                          (DE compound, shared stem)
    ISO 45001              <-> Arbeitssicherheitsmanagement            (norm / discipline)

Whether two spellings name the SAME competence is a JUDGEMENT (ADR-062 clause 1), not a
fact a containment/Jaccard predicate can compute without risking a false merge (SAP PP
must never fold into SAP MM; ISO 9001 must never fold into ISO 45001) -- so the fix is a
new sub-rule in the WRITER prompts, not a widened ``skills_page_dupe``. Two independent
builders emit the CV's `skills` field and each states its own version of this rule --
cv_tailoring.SYSTEM_PROMPT (single-call path, paired with build_user_prompt --
services/cv.py:470-483) and cv_segmented.SKILLS_SECTION_SYSTEM_PROMPT (segmented path,
paired with build_skills_prompt -- services/cv.py:394-401). The ORIGINAL "one entry per
competence" sentence had shipped only to the single-call prompt; per
test_jd_requirement_phrase_not_skill_prompts.py's own stated precedent for this exact
pair of files, a rule written into only one is no rule at the other.

A prompt rule cannot be proven by a unit test -- ADR-062 clause 7 requires a real-provider
charter run for that. These tests are PINS on what is deterministically checkable: the
rule text is present at both call sites (string-level, no LLM, mirrors
test_cv_budget_prompts.py / test_skill_verbatim_prompts.py in this directory), the
anti-overmerge guardrail names the boundary pairs explicitly, ``skills_page_dupe``'s
behaviour is UNCHANGED (still catches the three containment pairs, still misses the four
semantic pairs -- this fix touches no predicate), the five-pass skills pipeline order
downstream of the writer is unchanged, and no new numeric cap was added (CV_MAX_SKILLS
stays at its existing default).
"""

import inspect

from applire.prompts.cv_segmented import SKILLS_SECTION_SYSTEM_PROMPT, build_skills_prompt
from applire.prompts.cv_tailoring import SYSTEM_PROMPT, build_user_prompt

_JOB = {"role_title": "Produktionsleiter", "required_skills": [], "keywords": []}
_PROFILE = {"work_experience": []}


def _normalize(text: str) -> str:
    return " ".join(text.split())


# The four pairs this rule must teach the model to treat as ONE competence -- named
# verbatim so a future edit can't silently drop one. Each is the exact wording used in
# both prompts' examples.
_SEMANTIC_DEDUP_EXAMPLES = [
    "an acronym beside its expansion or description (MES / Maschinen- und "
    "Betriebsdatenerfassung)",
    "close synonyms (Lean Management / Lean Production)",
    "a compound sharing another entry's stem (Budgetverantwortung / Budgetplanung)",
    "a norm beside the discipline it belongs to (ISO 45001 / "
    "Arbeitssicherheitsmanagement)",
]

# The anti-overmerge guardrail: two pairs that must stay apart however similar the
# labels look. Present verbatim, identically, in both prompts.
_ANTI_OVERMERGE_CLAUSE = (
    "SAP PP stays apart from SAP MM, ISO 9001 stays apart from ISO 45001"
)

# The survivor-priority closing sentence -- present verbatim, identically, in both
# prompts (each reaches it by a slightly different route: cv_tailoring cross-references
# "rule 8's test" by number, cv_segmented restates the test inline, since that file has
# no rule numbering -- but the resolution sentence itself must not drift between them).
_SURVIVOR_PRIORITY_CLAUSE = (
    "otherwise keep the profile's own term, never the job ad's wording where the two "
    "differ, and never a phrasing you invent"
)


def test_single_call_writer_states_all_four_semantic_dedup_shapes():
    """cv_tailoring.build_user_prompt is the user-prompt half of the single-call writer
    call; services/cv.py:480 pairs it with ``system=SYSTEM_PROMPT``."""
    user_prompt = build_user_prompt(_JOB, _PROFILE, [], "de")
    assert user_prompt
    built = _normalize(SYSTEM_PROMPT + "\n\n" + user_prompt)
    for example in _SEMANTIC_DEDUP_EXAMPLES:
        assert example in built, f"single-call writer prompt is missing: {example!r}"


def test_segmented_skills_writer_states_all_four_semantic_dedup_shapes():
    """cv_segmented.build_skills_prompt is the user-prompt half of the segmented skills
    call; services/cv.py:400 pairs it with ``system=SKILLS_SECTION_SYSTEM_PROMPT``."""
    user_prompt = build_skills_prompt({}, _JOB, _PROFILE, [], "de")
    assert user_prompt
    built = _normalize(SKILLS_SECTION_SYSTEM_PROMPT + "\n\n" + user_prompt)
    for example in _SEMANTIC_DEDUP_EXAMPLES:
        assert example in built, f"segmented skills writer prompt is missing: {example!r}"


def test_both_builder_sites_state_the_anti_overmerge_guardrail_identically():
    """The task this rule exists to solve explicitly warns against over-merging: SAP PP
    is not SAP MM, ISO 9001 is not ISO 45001. Both writer prompts must name this
    boundary in the SAME words, or one path could learn a looser bar than the other."""
    sites = {
        "cv_tailoring.SYSTEM_PROMPT": SYSTEM_PROMPT,
        "cv_segmented.SKILLS_SECTION_SYSTEM_PROMPT": SKILLS_SECTION_SYSTEM_PROMPT,
    }
    for name, prompt in sites.items():
        assert _ANTI_OVERMERGE_CLAUSE in _normalize(prompt), (
            f"{name} is missing the anti-overmerge guardrail"
        )


def test_both_builder_sites_state_the_survivor_priority_identically():
    """When two entries do collapse into one, which spelling wins must not drift
    between the two writer paths (ADR-066: one logical operation, one contract)."""
    sites = {
        "cv_tailoring.SYSTEM_PROMPT": SYSTEM_PROMPT,
        "cv_segmented.SKILLS_SECTION_SYSTEM_PROMPT": SKILLS_SECTION_SYSTEM_PROMPT,
    }
    for name, prompt in sites.items():
        assert _SURVIVOR_PRIORITY_CLAUSE in _normalize(prompt), (
            f"{name} is missing the survivor-priority resolution"
        )


def test_survivor_priority_rejects_job_ad_wording_as_the_default():
    """This rule deliberately does NOT pick 'whichever form the job ad uses' (that
    heuristic was replayed against the pinned 2026-08-15 fixture and shown to risk total
    silent deletion via services/cv.py's _drop_ungrounded_jd_echo_skills -- see this
    file's v12 changelog entry). Pin the rejection: the profile's own term wins, the job
    ad's wording is the one named as the loser when the two differ."""
    normalized = _normalize(SYSTEM_PROMPT)
    idx = normalized.index(_SURVIVOR_PRIORITY_CLAUSE)
    # "never the job ad's wording" must appear strictly AFTER "keep the profile's own
    # term" in the same clause -- i.e. the profile wins, not the job ad.
    assert normalized.index("keep the profile's own term", idx) < normalized.index(
        "never the job ad's wording", idx
    )


def test_single_call_prompt_resolves_the_bullet_naming_collision():
    """Regel 7's OWN third sentence ("every skill you name in a bullet must also appear
    in the skills list") could otherwise read as demanding a SECOND entry for the form
    just merged away -- the pinned 2026-08-15 fixture's own bullet narrates both ("...
    eines MES-Systems zur Maschinen- und Betriebsdatenerfassung...") in one sentence.
    Only the single-call prompt can state this: the segmented skills call has no bullets
    in scope to name (they are generated by a separate, decoupled call)."""
    normalized = _normalize(SYSTEM_PROMPT)
    assert "in whichever form you kept" in normalized
    assert "does not need a second entry" in normalized


def test_skills_page_dupe_still_catches_the_three_previously_fixed_pairs():
    """Regression pin: this change touches no predicate. The three pairs the audit
    found ALREADY collapsed on the delivered document must keep collapsing."""
    from applire.services.ats_audit import skills_page_dupe

    already_caught = [
        ("Feinplanung/Fertigungssteuerung", "Fertigungssteuerung"),
        ("KVP", "KVP/Kaizen"),
        ("SAP", "SAP PP"),
    ]
    for a, b in already_caught:
        assert skills_page_dupe(a, b), f"skills_page_dupe regressed on {a!r} / {b!r}"


def test_skills_page_dupe_still_misses_the_four_semantic_pairs():
    """Confirms the diagnosis this rule is built on, and that the fix was NOT made by
    quietly widening skills_page_dupe (task instruction: do not touch it). If this ever
    flips to True, the prompt-side rule above has become partially redundant with a code
    change made elsewhere -- re-check for a double-fix, not a free win."""
    from applire.services.ats_audit import skills_page_dupe

    still_missed = [
        ("MES", "Maschinen- und Betriebsdatenerfassung"),
        ("Lean Management", "Lean Production"),
        ("Budgetverantwortung", "Budgetplanung"),
        ("ISO 45001", "Arbeitssicherheitsmanagement"),
    ]
    for a, b in still_missed:
        assert not skills_page_dupe(a, b), (
            f"skills_page_dupe now catches {a!r} / {b!r} -- ats_audit.py changed "
            "underneath this prompt fix"
        )


def test_sap_pp_and_sap_mm_are_never_treated_as_one_entry():
    """Dedicated negative case (task requirement): SAP PP and SAP MM are two different
    ERP modules and must never collapse, however similar the labels look. Two
    independent guarantees: the deterministic predicate stays False, AND the prompt
    names this exact pair as one that must stay apart."""
    from applire.services.ats_audit import skills_page_dupe

    assert not skills_page_dupe("SAP PP", "SAP MM")
    assert "SAP PP stays apart from SAP MM" in _normalize(SYSTEM_PROMPT)
    assert "SAP PP stays apart from SAP MM" in _normalize(SKILLS_SECTION_SYSTEM_PROMPT)


def test_iso_9001_and_iso_45001_are_never_treated_as_one_entry():
    """Dedicated negative case (task requirement): ISO 9001 (quality) and ISO 45001
    (safety) are two different norms and must never collapse into one entry, even
    though the new rule explicitly asks the model to fold a NORM into the general
    DISCIPLINE it governs (ISO 45001 / Arbeitssicherheitsmanagement) -- that merge
    direction must never be read as licence to merge across norms themselves."""
    from applire.services.ats_audit import skills_page_dupe

    assert not skills_page_dupe("ISO 9001", "ISO 45001")
    assert "ISO 9001 stays apart from ISO 45001" in _normalize(SYSTEM_PROMPT)
    assert "ISO 9001 stays apart from ISO 45001" in _normalize(SKILLS_SECTION_SYSTEM_PROMPT)


def test_skills_pipeline_pass_order_after_the_writer_is_unchanged():
    """Fate-check pin (applire-prompt-first step 2b): the five deterministic passes
    that touch `tailored.skills` after either writer run in services/cv.py
    ``_compose_document``, in this exact order -- unchanged by this prompt-only fix.
    ``_drop_ungrounded_jd_echo_skills`` (position 2) is the pass that can silently
    DELETE a merged survivor with no page-dupe tie to any vault-attested spelling if a
    future edit ever picks 'the job ad's wording' as the survivor -- this is why that
    heuristic was rejected above, and why this order matters to keep pinned.
    """
    from applire.services import cv

    src = inspect.getsource(cv._compose_document)
    names = [
        "_dedup_skills(",
        "_drop_ungrounded_jd_echo_skills(",
        "_tailor_skills_to_jd(",
        "_restore_skill_spelling(",
        "_restore_narrative_named_skills(",
    ]
    positions = [src.index(n) for n in names]
    assert positions == sorted(positions), (
        "the skills pipeline's pass order changed -- re-check the fate analysis in "
        "cv_tailoring.py's v12 changelog entry (KNOWN RESIDUAL RISK note)"
    )


def test_cv_max_skills_cap_default_is_unchanged():
    """No new prompt-side numeric ceiling was added (see cv_tailoring.py's v12
    changelog entry for why: CV_MAX_SKILLS already bounds the list downstream, and a
    tier-0/JD-required skill bypasses it by design -- the 25-entry overage is the
    mechanical result of unmerged surface forms each independently earning tier-0, not
    evidence the existing cap is wrong). Pins the existing default so a future change to
    it is a deliberate, separate decision, not a silent side effect of this fix."""
    from applire.constants import CV_MAX_SKILLS

    assert CV_MAX_SKILLS == 24
