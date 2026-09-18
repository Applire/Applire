# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Bug #723 — an answered attribution question writes what it holds.

The harm, edge UAT 2026-09-18 (`ec2e6af5`, agent door, gap-fill interview):
the attribution guard held a bullet, asked "Wohin gehört es?", the candidate
answered "move", and the resolution wrote `metadata|updated|
pending_confirmations` and nothing else. The bullet existed in no role
afterwards, under a receipt reading "Recorded your answer to a confirmation
question". Twice in one run, `triage:vault-integrity`.

**Every fixture here is a SYNTHETIC TWIN.** The Bug body quotes the founder's
real bullets as evidence; they are evidence, not fixture text. What is
reproduced is the SHAPE: two employers, several roles at the anchor one, a
German answer whose clause names the OTHER employer, English bullets, and a
`technologies` list riding along with them.
"""
from __future__ import annotations

import pytest

from applire.schemas.profile import (
    MasterProfileData,
    PendingConfirmation,
    ProfileMetadata,
    WorkEntry,
)
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.attribution import (
    enforce_attribution,
    plan_attribution_resolution,
)
from applire.services.profile.reconcile.ops import (
    AddBullets,
    RequestConfirmation,
    ResolveConfirmation,
)

PREV_ID = "w-westfalen"
ANCHOR_2018 = "w-nordlicht-2018"
ANCHOR_2021 = "w-nordlicht-2021"
ANCHOR_2023 = "w-nordlicht-2023"

# The held content, synthetic. Bullet 1 names the anchor employer in its own
# words (channel 2 sees it); bullet 2 and the technologies do not (only the
# batch-level channel can).
HELD_NAMED = (
    "After joining Nordlicht Biotech in 2018, restructured how the team works "
    "and taught a curriculum of three software-engineering courses"
)
HELD_SIBLING = "Took over team leadership during the team lead's parental leave"
HELD_TECH_A = "Clean Code"
HELD_TECH_B = "Software architecture"

# A German answer that names ONE employer — the anchor — while the reconciler
# targets the previous one. The bullets above are its English paraphrase, which
# is why the owning-sentence channel (token overlap) cannot reach them.
ANSWER_DE = (
    "Als ich 2018 zu Nordlicht Biotech kam, habe ich die Arbeitsweise des "
    "Teams umgestellt, neue Prozesse definiert und ein Curriculum aus drei "
    "Software-Engineering-Kursen unterrichtet. Außerdem habe ich während der "
    "Elternzeit der Teamleitung die Teamleitung übernommen."
)


def _profile(anchor_roles: int = 3) -> MasterProfileData:
    """Two employers; the anchor one held under `anchor_roles` roles."""
    roles = [
        WorkEntry(id=ANCHOR_2023, company="Nordlicht Biotech SE",
                  role="Head of Engineering", start_date="2023-07", is_current=True),
        WorkEntry(id=ANCHOR_2021, company="Nordlicht Biotech SE",
                  role="Team Lead Platform", start_date="2021-01", end_date="2023-06"),
        WorkEntry(id=ANCHOR_2018, company="Nordlicht Biotech SE",
                  role="Senior Software Engineer", start_date="2018-10", end_date="2020-12"),
    ][:anchor_roles] if anchor_roles else []
    return MasterProfileData(
        work_experience=[
            *roles,
            WorkEntry(id=PREV_ID, company="Westfalen Blutspendedienst gGmbH",
                      role="IT Systems Lead", start_date="2012-01", end_date="2018-09"),
        ]
    )


def _mis_targeted_batch() -> list:
    return [
        AddBullets(
            target=PREV_ID,
            responsibilities=[HELD_NAMED, HELD_SIBLING],
            technologies=[HELD_TECH_A, HELD_TECH_B],
        )
    ]


def _guarded(profile: MasterProfileData) -> list:
    return enforce_attribution(
        _mis_targeted_batch(),
        profile=profile,
        new_info={"answer": ANSWER_DE},
        source="interview",
    )


def _confirmation_of(ops: list) -> RequestConfirmation:
    confs = [
        o
        for o in ops
        if isinstance(o, RequestConfirmation) and o.context.get("anchor_employer")
    ]
    assert len(confs) == 1, f"expected one attribution ask, got {len(confs)}"
    return confs[0]


def _parked(profile: MasterProfileData, conf: RequestConfirmation) -> MasterProfileData:
    """The profile with the ask parked, as `commit_ops` persists it."""
    out = profile.model_copy(deep=True)
    out.metadata = ProfileMetadata(
        pending_confirmations=[
            PendingConfirmation(
                confirmation_id="c-1",
                question=conf.question,
                options=list(conf.options),
                context=dict(conf.context),
                question_i18n=conf.question_i18n,
                options_i18n=conf.options_i18n,
                option_keys=list(conf.option_keys),
            )
        ]
    )
    return out


def _answer(profile: MasterProfileData, chosen: str):
    return apply_ops(
        profile,
        [ResolveConfirmation(confirmation_id="c-1", chosen_option=chosen)],
        "manual_edit",
    )


def _option(conf: RequestConfirmation, key: str, lang: str = "de") -> str:
    idx = conf.option_keys.index(key)
    return (conf.options_i18n or [])[idx][lang]


def _bullets(profile: MasterProfileData, entry_id: str) -> list[str]:
    entry = next(w for w in profile.work_experience if w.id == entry_id)
    return [*entry.responsibilities, *entry.achievements, *entry.technologies]


# ── the guard's clause scoping: the sibling and the technologies are held ────


def test_the_whole_clause_is_held_not_only_the_bullet_that_repeats_the_name():
    """#723's unflagged sibling + #674 line 34's other half.

    Before: only `HELD_NAMED` was pulled out (its own words name the anchor);
    the sibling and BOTH technologies merged silently under the previous
    employer. The batch-level channel holds all four — the batch writes guarded
    content to exactly ONE employer and the answer names exactly one other.
    """
    ops = _guarded(_profile())
    conf = _confirmation_of(ops)
    held = {item["text"] for item in conf.context["flagged"]}
    assert held == {HELD_NAMED, HELD_SIBLING, HELD_TECH_A, HELD_TECH_B}
    assert [o for o in ops if isinstance(o, AddBullets) and o.target == PREV_ID] == []


def test_the_batch_channel_is_silent_when_the_answer_names_the_target_itself():
    """Negative control: same shape, an answer about the employer the op targets.
    Nothing is held — over-drop discipline is what makes the guard usable."""
    answer = (
        "Beim Westfalen Blutspendedienst habe ich die Arbeitsweise des Teams "
        "umgestellt und ein Curriculum aus drei Kursen unterrichtet."
    )
    ops = enforce_attribution(
        [AddBullets(target=PREV_ID, responsibilities=[HELD_SIBLING],
                    technologies=[HELD_TECH_A])],
        profile=_profile(),
        new_info={"answer": answer},
        source="interview",
    )
    assert not [o for o in ops if isinstance(o, RequestConfirmation)]


def test_the_batch_channel_is_silent_when_the_batch_names_two_employers():
    """Negative control: the model split the answer across both employers, so
    there is nothing for a batch-level anchor to contradict."""
    ops = enforce_attribution(
        [
            AddBullets(target=PREV_ID, responsibilities=[HELD_SIBLING]),
            AddBullets(target=ANCHOR_2018, responsibilities=[HELD_NAMED]),
        ],
        profile=_profile(),
        new_info={"answer": ANSWER_DE},
        source="interview",
    )
    held = [
        o for o in ops
        if isinstance(o, RequestConfirmation) and o.context.get("anchor_employer")
    ]
    assert held == []


# ── the resolution writes — one test per answer key × cardinality ────────────


def test_move_with_one_candidate_entry_writes_the_bullets():
    profile = _profile(anchor_roles=1)  # only the 2023 role at the anchor
    conf = _confirmation_of(_guarded(profile))
    result = _answer(_parked(profile, conf), _option(conf, "move"))

    landed = _bullets(result.profile, ANCHOR_2023)
    assert HELD_NAMED in landed and HELD_SIBLING in landed
    assert HELD_TECH_A in landed and HELD_TECH_B in landed
    assert _bullets(result.profile, PREV_ID) == []
    work_changes = [c for c in result.changes if c.section == "work_experience"]
    assert work_changes, "a decision that names a bullet may not receipt metadata only"


def test_move_with_several_candidates_uses_the_year_in_the_held_text():
    """Founder ruling V-1 / D-6: the year is a FACT in the text we persisted.
    Three roles at the anchor; only the 2018-10 one contains 2018."""
    profile = _profile(anchor_roles=3)
    conf = _confirmation_of(_guarded(profile))
    result = _answer(_parked(profile, conf), _option(conf, "move"))

    assert HELD_NAMED in _bullets(result.profile, ANCHOR_2018)
    assert HELD_SIBLING in _bullets(result.profile, ANCHOR_2018)
    assert _bullets(result.profile, ANCHOR_2021) == []
    assert _bullets(result.profile, ANCHOR_2023) == []
    assert not result.pending_confirmations


def test_move_with_several_candidates_and_no_discriminating_year_asks_again():
    """Ruling V-1 excludes option C: no "most recent role" guess. The narrower
    ask is parked, keyed on entry IDS, and the content stays held on the
    receipt — never silently dropped, never silently placed."""
    profile = _profile(anchor_roles=3)
    conf = _confirmation_of(_guarded(profile))
    context = dict(conf.context)
    context["flagged"] = [{"field": "responsibilities", "text": HELD_SIBLING}]
    parked = _parked(profile, conf)
    parked.metadata.pending_confirmations[0].context = context

    result = _answer(parked, _option(conf, "move"))

    assert all(_bullets(result.profile, i) == [] for i in
               (ANCHOR_2018, ANCHOR_2021, ANCHOR_2023))
    assert [i.reason for i in result.not_applied] == ["confirmation_held"]
    assert HELD_SIBLING[:40] in result.not_applied[0].label
    followup = result.pending_confirmations[0]
    assert followup.option_keys == [
        f"entry:{ANCHOR_2023}", f"entry:{ANCHOR_2021}", f"entry:{ANCHOR_2018}", "discard",
    ]
    assert "2018-10" in " ".join(followup.options)


def test_the_year_is_only_a_discriminator_when_exactly_one_role_contains_it():
    """A year TWO candidate roles contain does not place anything — the ask wins.
    2021 falls inside both the 2021-01→2023-06 role and, as an open-ended one
    would, nothing else; here the 2018-10→2022-12 overlap makes it two."""
    profile = _profile(anchor_roles=3)
    profile.work_experience[2].end_date = "2022-12"  # the 2018 role now covers 2021
    conf = _confirmation_of(_guarded(profile))
    context = dict(conf.context)
    context["flagged"] = [
        {"field": "responsibilities", "text": "Ran the 2021 platform migration"}
    ]
    parked = _parked(profile, conf)
    parked.metadata.pending_confirmations[0].context = context

    result = _answer(parked, _option(conf, "move"))
    assert [i.reason for i in result.not_applied] == ["confirmation_held"]
    assert result.pending_confirmations


def test_answering_the_narrower_ask_by_entry_id_writes_the_bullets():
    """The loop closes: family 5's own keys resolve through the same path."""
    profile = _profile(anchor_roles=3)
    conf = _confirmation_of(_guarded(profile))
    context = dict(conf.context)
    context["flagged"] = [{"field": "responsibilities", "text": HELD_SIBLING}]
    parked = _parked(profile, conf)
    parked.metadata.pending_confirmations[0].context = context
    held = _answer(parked, _option(conf, "move"))

    followup = held.pending_confirmations[0]
    stage2 = held.profile.model_copy(deep=True)
    stage2.metadata.pending_confirmations = [
        PendingConfirmation(
            confirmation_id="c-2", question=followup.question,
            options=list(followup.options), context=dict(followup.context),
            question_i18n=followup.question_i18n,
            options_i18n=followup.options_i18n,
            option_keys=list(followup.option_keys),
        )
    ]
    idx = followup.option_keys.index(f"entry:{ANCHOR_2021}")
    result = apply_ops(
        stage2,
        [ResolveConfirmation(confirmation_id="c-2",
                             chosen_option=(followup.options_i18n or [])[idx]["de"])],
        "manual_edit",
    )
    assert HELD_SIBLING in _bullets(result.profile, ANCHOR_2021)
    assert not result.not_applied


def test_move_with_no_candidate_entry_records_the_loss_instead_of_swallowing_it():
    profile = _profile(anchor_roles=3)
    conf = _confirmation_of(_guarded(profile))
    parked = _parked(profile, conf)
    parked.work_experience = [w for w in parked.work_experience if w.id == PREV_ID]

    result = _answer(parked, _option(conf, "move"))
    assert {i.reason for i in result.not_applied} == {"confirmation_unresolvable"}
    assert len(result.not_applied) == 4


def test_keep_here_appends_to_the_entry_the_op_originally_targeted():
    profile = _profile(anchor_roles=3)
    conf = _confirmation_of(_guarded(profile))
    result = _answer(_parked(profile, conf), _option(conf, "keep_here"))

    landed = _bullets(result.profile, PREV_ID)
    assert {HELD_NAMED, HELD_SIBLING, HELD_TECH_A, HELD_TECH_B} <= set(landed)
    assert [c.section for c in result.changes].count("work_experience") >= 1


def test_discard_drops_with_a_receipt_line_that_says_so():
    profile = _profile(anchor_roles=3)
    conf = _confirmation_of(_guarded(profile))
    result = _answer(_parked(profile, conf), _option(conf, "discard"))

    assert all(_bullets(result.profile, i) == [] for i in (PREV_ID, ANCHOR_2018))
    assert {i.reason for i in result.not_applied} == {"confirmation_discarded"}
    assert len(result.not_applied) == 4


def test_a_bullet_the_entry_already_carries_is_receipted_not_silent():
    """`_append_dedup` suppresses the append (ADR-082), so `changes` stays quiet
    — and a metadata-only receipt for a decision that names a bullet is the
    exact #723 harm. The receipt says it on the `not_applied` channel."""
    profile = _profile(anchor_roles=1)
    profile.work_experience[0].responsibilities = [HELD_NAMED, HELD_SIBLING]
    profile.work_experience[0].technologies = [HELD_TECH_A, HELD_TECH_B]
    conf = _confirmation_of(_guarded(profile))
    result = _answer(_parked(profile, conf), _option(conf, "move"))

    assert {i.reason for i in result.not_applied} == {"confirmation_already_present"}
    assert _bullets(result.profile, ANCHOR_2023).count(HELD_NAMED) == 1


# ── the invariant, stated once over every answer key ─────────────────────────


@pytest.mark.parametrize("key", ["move", "keep_here", "discard"])
def test_no_answer_key_leaves_a_metadata_only_receipt(key: str):
    """#723's invariant. Before this build every one of the three wrote exactly
    one change — `metadata|updated|pending_confirmations` — and nothing else."""
    profile = _profile(anchor_roles=3)
    conf = _confirmation_of(_guarded(profile))
    result = _answer(_parked(profile, conf), _option(conf, key))

    content = [c for c in result.changes if c.section != "metadata"]
    assert content or result.not_applied, (
        f"'{key}' produced a metadata-only receipt for a decision naming a bullet"
    )


def test_an_english_render_of_the_same_option_resolves_identically():
    """#669's contract: the answer's identity is the KEY, not the rendering."""
    profile = _profile(anchor_roles=1)
    conf = _confirmation_of(_guarded(profile))
    de = _answer(_parked(profile, conf), _option(conf, "move", "de"))
    en = _answer(_parked(profile, conf), _option(conf, "move", "en"))
    assert _bullets(de.profile, ANCHOR_2023) == _bullets(en.profile, ANCHOR_2023)


def test_a_confirmation_without_stable_keys_keeps_bookkeeping_only_behaviour():
    """A model-emitted `request_confirmation` (prompt rule 6) carries no
    `option_keys` and no `flagged` context — nothing was held, so nothing is
    written. Back-compat for records persisted before #669, too."""
    assert plan_attribution_resolution({"section": "work_experience"}, None, _profile()) is None
    assert plan_attribution_resolution(
        {"section": "work_experience", "anchor_employer": "Nordlicht Biotech SE",
         "flagged": [{"field": "responsibilities", "text": HELD_SIBLING}]},
        None,
        _profile(),
    ) is None


# ── #674 line 34: the sentence anchor and the model's own partition ──────────

# Exactly ONE of the three stations is nameable against the vault: "Nordlicht
# Biotech" matches, "the blood donation service" is a gloss of Westfalen
# Blutspendedienst, and the third clause names no company at all. That is the
# shape #243's "two or more employers → fail open" cannot see, measured 23/29 on
# the captured #684 records.
_THREE_STATIONS = (
    "15+ years in pharmaceutical manufacturing — monoclonal antibodies at "
    "Nordlicht Biotech, blood bags at the blood donation service and now mRNA "
    "vaccines and personalised cancer vaccines."
)


def _three_station_profile() -> MasterProfileData:
    return MasterProfileData(
        work_experience=[
            WorkEntry(id="w-sued", company="Südwind Therapeutics GmbH",
                      role="Director Supply Chain", start_date="2019-03", is_current=True),
            WorkEntry(id=PREV_ID, company="Westfalen Blutspendedienst gGmbH",
                      role="IT Systems Lead", start_date="2012-01", end_date="2019-02"),
            WorkEntry(id=ANCHOR_2018, company="Nordlicht Biotech SE",
                      role="Systems Engineer", start_date="2005-09", end_date="2011-12"),
        ]
    )


def test_a_sentence_the_model_partitioned_anchors_nothing():
    """#674 line 34. The sentence names three stations but only ONE of them
    matches a vault company literally ("Nordlicht Biotech"; "Südwind
    Therapeutics" is there too but "the blood donation service" is a gloss), so
    #243's "two or more employers → fail open" never engaged and every
    correctly-targeted bullet of the sentence was asked about against the one
    name that matched. The model gave each employer its OWN share — that is a
    partition, and a partition silences the anchor."""
    ops = enforce_attribution(
        [
            AddBullets(target="w-sued", responsibilities=["mRNA vaccines"]),
            AddBullets(target=PREV_ID, responsibilities=["Blood bags"]),
            AddBullets(target=ANCHOR_2018, responsibilities=["Monoclonal antibodies"]),
        ],
        profile=_three_station_profile(),
        new_info={"answer": _THREE_STATIONS},
        source="interview",
    )
    assert not [
        o for o in ops
        if isinstance(o, RequestConfirmation) and o.context.get("anchor_employer")
    ]


def test_the_same_text_given_to_two_employers_is_not_a_partition():
    """#243's own live shape, abstracted: the model handed ONE bullet to TWO
    employers. That is not a split, it is the model contradicting itself, and
    the anchor must still speak — otherwise the fix for line 34 would delete
    the guard's founding regression."""
    shared = "Monoclonal antibodies"
    ops = enforce_attribution(
        [
            AddBullets(target=ANCHOR_2018, achievements=[shared]),
            AddBullets(target="w-sued", achievements=[shared]),
        ],
        profile=_three_station_profile(),
        new_info={"answer": _THREE_STATIONS},
        source="interview",
    )
    flagged = [
        o for o in ops
        if isinstance(o, RequestConfirmation) and o.context.get("anchor_employer")
    ]
    assert len(flagged) == 1
    assert flagged[0].context["target_employer"].startswith("Südwind")
