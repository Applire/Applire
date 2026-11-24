# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Sorbet invite-gate WP-V: the employer guard holds a batch's evidence links too,
and the reconcile input view renders each entry's identity first.

Delivery-2a rec 215 (and the same turn in the 2b recheck, rec 189): Stefan
answered "… habe ich bei Weberit die Vorbereitung der ISO-45001-Zertifizierung
für die Produktion begleitet …"; Luna put the bullets AND both skills'
``evidence`` on his previous employer Rasselstein. The guard held the bullets
(q6, short-form anchor) and the skills kept their link to Rasselstein.

Prompt-first: the input view was the producer (``id`` and ``company`` rendered
after the bullets): 3/5 wrong as captured, 0/5 company-first, 0/5 id-first
(luna, reasoning off, n=5 each). The guard is the backstop and now follows
every op kind that names a work entry by reference: ``add_bullets`` (target),
``set_field`` (target; free text since M-2c, size / budget / location / dates
since adv/vault F-5), ``upsert_work`` on an existing entry (its scalar fills,
F-5), ``upsert_project`` (parent, F-5), ``upsert_skill`` / ``upsert_story``
(evidence) — and never asks about a link the vault already holds (F-6).

**Every fixture here is SYNTHETIC** (the delivery runs' invented personas).
"""
from __future__ import annotations

import json

from applire.prompts.reconcile import _identity_first, build_reconcile_prompt
from applire.schemas.profile import (
    MasterProfileData,
    PendingConfirmation,
    ProfileMetadata,
    ProjectEntry,
    Skill,
    WorkEntry,
)
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.attribution import enforce_attribution
from applire.services.profile.reconcile.ops import (
    AddBullets,
    RequestConfirmation,
    ResolveConfirmation,
    UpsertSkill,
    UpsertStory,
)

_WEB, _RAS = "w-weberit", "w-rasselstein"
_ANSWER = (
    "Ich bin ausgebildeter Sicherheitsbeauftragter und habe bei Weberit die Vorbereitung der "
    "ISO-45001-Zertifizierung für die Produktion begleitet: Gefährdungsbeurteilungen "
    "überarbeitet, Unterweisungssystem neu aufgesetzt. Die Unfallquote (LTIF) in meinem "
    "Bereich habe ich in fünf Jahren von 8,2 auf 3,1 gesenkt. Die Zertifizierung selbst hat "
    "der QHSE-Leiter verantwortet, ich war der Produktionspart."
)
_RESP = (
    "Vorbereitung der ISO-45001-Zertifizierung für die Produktion begleitet; "
    "Gefährdungsbeurteilungen überarbeitet und Unterweisungssystem neu aufgesetzt."
)
_ACH = "Unfallquote (LTIF) im eigenen Bereich in fünf Jahren von 8,2 auf 3,1 gesenkt."


def _profile(*, skills: bool = False) -> MasterProfileData:
    return MasterProfileData(
        work_experience=[
            WorkEntry(id=_WEB, company="Weberit Kunststofftechnik GmbH",
                      role="Produktionsleiter", start_date="2017-04", is_current=True,
                      responsibilities=["Verantwortung für zwei Fertigungsbereiche."]),
            WorkEntry(id=_RAS, company="Rasselstein Umformtechnik GmbH",
                      role="Fertigungsmeister / Schichtleiter", start_date="2011-08",
                      end_date="2017-03",
                      responsibilities=["Führung einer Schicht mit 14 Mitarbeitenden."]),
        ],
        skills=[Skill(name="ISO 45001", category="domain")] if skills else [],
    )


def _ni(answer: str = _ANSWER) -> dict:
    return {"gap": "Arbeitssicherheit und Normen", "question": "Welche Erfahrungen …?", "answer": answer}


def _batch(target: str = _RAS, evidence: str | None = None) -> list:
    ev = [evidence or target]
    return [
        UpsertSkill(name="Arbeitssicherheitsmanagement", category="domain", evidence=ev),
        UpsertSkill(name="ISO 45001", category="domain", evidence=ev),
        AddBullets(target=target, responsibilities=[_RESP], achievements=[_ACH]),
    ]


def _asks(out: list) -> list[RequestConfirmation]:
    return [o for o in out if isinstance(o, RequestConfirmation) and (o.context or {}).get("anchor_employer")]


def _skill_ops(out: list) -> dict[str, list[str]]:
    return {o.name: list(o.evidence) for o in out if isinstance(o, UpsertSkill)}


# ── the guard ────────────────────────────────────────────────────────────────


def test_rec_215_skill_evidence_is_held_with_the_bullets_in_one_ask():
    out = enforce_attribution(_batch(), profile=_profile(), new_info=_ni(), source="interview")
    asks = _asks(out)
    assert len(asks) == 1, "one question for the whole turn, never a second one for the links"
    flagged = asks[0].context["flagged"]
    assert {"field": "skill_evidence", "text": "ISO 45001"} in flagged
    assert {"field": "skill_evidence", "text": "Arbeitssicherheitsmanagement"} in flagged
    # the skills themselves are written; only the pointer waits for the person
    assert _skill_ops(out) == {"Arbeitssicherheitsmanagement": [], "ISO 45001": []}


def test_the_right_target_keeps_every_link():
    out = enforce_attribution(_batch(target=_WEB), profile=_profile(), new_info=_ni(), source="interview")
    assert _asks(out) == []
    assert _skill_ops(out) == {"Arbeitssicherheitsmanagement": [_WEB], "ISO 45001": [_WEB]}


def test_a_link_to_an_entry_that_kept_content_is_not_held():
    """The answer named two employers and the model split it: one bullet per
    employer, the skill on the entry whose bullet stayed. Nothing held."""
    answer = (
        "Bei Weberit habe ich Shopfloor-Management eingeführt. Bei Rasselstein habe ich "
        "die ISO-45001-Unterweisungen für meine Schicht organisiert."
    )
    ops = [
        UpsertSkill(name="ISO 45001", category="domain", evidence=[_RAS]),
        AddBullets(target=_WEB, responsibilities=["Shopfloor-Management eingeführt."]),
        AddBullets(target=_RAS, responsibilities=["ISO-45001-Unterweisungen für die Schicht organisiert."]),
    ]
    out = enforce_attribution(ops, profile=_profile(), new_info=_ni(answer), source="interview")
    assert _asks(out) == []
    assert _skill_ops(out) == {"ISO 45001": [_RAS]}


def test_a_story_link_follows_the_held_bullets_too():
    ops = [
        UpsertStory(title="ISO-45001-Vorbereitung", challenge="c", mechanism="m",
                    outcome="LTIF von 8,2 auf 3,1", evidence=[_RAS]),
        AddBullets(target=_RAS, responsibilities=[_RESP], achievements=[_ACH]),
    ]
    out = enforce_attribution(ops, profile=_profile(), new_info=_ni(), source="interview")
    (ask,) = _asks(out)
    assert {"field": "story_evidence", "text": "ISO-45001-Vorbereitung"} in ask.context["flagged"]
    assert next(o for o in out if isinstance(o, UpsertStory)).evidence == []


def test_a_skill_only_link_against_the_batch_anchor_raises_its_own_ask():
    """Channel 3 (whole-answer anchor) reaches a link whose entry the batch did
    not write bullets to: the batch writes to Rasselstein only, the answer names
    Weberit only, and the skill also points at a third, project entry of
    Rasselstein's."""
    profile = _profile()
    profile.projects = [ProjectEntry(id="p-ras", name="Umbau Presse 3", associated_experience=_RAS)]
    ops = [
        UpsertSkill(name="ISO 45001", category="domain", evidence=["p-ras"]),
        AddBullets(target=_RAS, responsibilities=[_RESP], achievements=[_ACH]),
    ]
    out = enforce_attribution(ops, profile=profile, new_info=_ni(), source="interview")
    asks = _asks(out)
    link_asks = [a for a in asks if a.context.get("target") == "p-ras"]
    assert len(link_asks) == 1
    assert link_asks[0].context["flagged"] == [{"field": "skill_evidence", "text": "ISO 45001"}]
    assert link_asks[0].context["anchor_employer"] == "Weberit Kunststofftechnik GmbH"


def test_outside_the_interview_doors_nothing_is_held():
    out = enforce_attribution(_batch(), profile=_profile(), new_info=_ni(), source="cv_upload")
    assert _asks(out) == []
    assert _skill_ops(out) == {"Arbeitssicherheitsmanagement": [_RAS], "ISO 45001": [_RAS]}


# ── the resolution places the link where it places the bullets ──────────────


def _parked_after_turn() -> tuple[MasterProfileData, RequestConfirmation]:
    profile = _profile()
    out = enforce_attribution(_batch(), profile=profile, new_info=_ni(), source="interview")
    (ask,) = _asks(out)
    written = apply_ops(profile, [o for o in out if not isinstance(o, RequestConfirmation)], "interview").profile
    written.metadata = ProfileMetadata(pending_confirmations=[PendingConfirmation(
        confirmation_id="c-1", question=ask.question, options=list(ask.options),
        context=dict(ask.context), question_i18n=ask.question_i18n,
        options_i18n=ask.options_i18n, option_keys=list(ask.option_keys),
    )])
    return written, ask


def _option(ask: RequestConfirmation, key: str) -> str:
    return (ask.options_i18n or [])[ask.option_keys.index(key)]["de"]


def _refs(profile: MasterProfileData, name: str) -> list[str]:
    return next(s for s in profile.skills if s.name == name).experience_refs


def test_move_links_both_skills_to_the_named_employer():
    profile, ask = _parked_after_turn()
    assert _refs(profile, "ISO 45001") == []
    result = apply_ops(profile, [ResolveConfirmation(confirmation_id="c-1", chosen_option=_option(ask, "move"))],
                       "manual_edit")
    assert _refs(result.profile, "ISO 45001") == [_WEB]
    assert _refs(result.profile, "Arbeitssicherheitsmanagement") == [_WEB]
    web = next(w for w in result.profile.work_experience if w.id == _WEB)
    assert _RESP in web.responsibilities and _ACH in web.achievements
    assert any(c.field == "experience_refs" for c in result.changes)


def test_keep_here_puts_the_link_back():
    profile, ask = _parked_after_turn()
    result = apply_ops(profile, [ResolveConfirmation(confirmation_id="c-1", chosen_option=_option(ask, "keep_here"))],
                       "manual_edit")
    assert _refs(result.profile, "ISO 45001") == [_RAS]


def test_discard_leaves_the_skill_without_that_link_and_says_so():
    profile, ask = _parked_after_turn()
    result = apply_ops(profile, [ResolveConfirmation(confirmation_id="c-1", chosen_option=_option(ask, "discard"))],
                       "manual_edit")
    assert _refs(result.profile, "ISO 45001") == []
    labels = [n.label for n in result.not_applied]
    assert "skill_evidence: ISO 45001" in labels


def test_a_link_whose_skill_is_gone_is_receipted_not_lost():
    profile, ask = _parked_after_turn()
    profile.skills = [s for s in profile.skills if s.name != "ISO 45001"]
    result = apply_ops(profile, [ResolveConfirmation(confirmation_id="c-1", chosen_option=_option(ask, "move"))],
                       "manual_edit")
    assert any(n.label == "skill_evidence: ISO 45001" and n.reason == "confirmation_unresolvable"
               for n in result.not_applied)


# ── the input view: identity first ──────────────────────────────────────────


def _entry_keys(prompt: str, section: str) -> list[list[str]]:
    a = prompt.index("\n{", 0) + 1
    b = prompt.index("\n\nSOURCE:")
    view = json.loads(prompt[a:b])
    return [list(e.keys()) for e in view.get(section) or []]


def test_the_reconcile_prompt_renders_each_work_entry_id_company_role_first():
    profile = _profile()
    profile.projects = [ProjectEntry(id="p-ras", name="Umbau Presse 3", associated_experience=_RAS,
                                     responsibilities=["x"])]
    prompt = build_reconcile_prompt(profile, _ni(), "interview")
    for keys in _entry_keys(prompt, "work_experience"):
        assert keys[:3] == ["id", "company", "role"]
        assert keys.index("id") < keys.index("responsibilities")
    for keys in _entry_keys(prompt, "projects"):
        assert keys[:3] == ["id", "name", "associated_experience"]


def test_identity_first_changes_order_only():
    view = {"work_experience": [{"role": "R", "responsibilities": ["b"], "id": "w1", "company": "C", "x": 1}],
            "skills": [{"name": "S", "id": "s1"}]}
    out = _identity_first(view)
    assert list(out["work_experience"][0]) == ["id", "company", "role", "responsibilities", "x"]
    assert out["work_experience"][0] == view["work_experience"][0]
    assert out["skills"] == view["skills"]


# ── channel 1 on the skill's name (two employers named: no batch anchor) ─────

_TWO = (
    "Bei Rasselstein habe ich die SAP-Einführung in der Fertigung begleitet. "
    "Bei Weberit habe ich die ISO-45001-Zertifizierung für die Produktion vorbereitet."
)


def _two_batch() -> list:
    return [
        UpsertSkill(name="SAP", category="tool", evidence=[_RAS]),
        UpsertSkill(name="ISO 45001", category="domain", evidence=[_RAS]),
        AddBullets(target=_RAS, responsibilities=["ISO-45001-Zertifizierung für die Produktion vorbereitet."]),
    ]


def test_a_link_whose_name_sentence_names_another_employer_is_held():
    out = enforce_attribution(_two_batch(), profile=_profile(), new_info=_ni(_TWO), source="interview")
    asks = _asks(out)
    assert len(asks) == 1
    assert {"field": "skill_evidence", "text": "ISO 45001"} in asks[0].context["flagged"]
    assert _skill_ops(out)["ISO 45001"] == []


def test_a_link_whose_name_sentence_names_its_own_employer_is_kept():
    """The deleted 'every bullet of the entry was held' rule would have held the
    SAP link here: Rasselstein's only bullet of the batch is the Weberit one."""
    out = enforce_attribution(_two_batch(), profile=_profile(), new_info=_ni(_TWO), source="interview")
    assert _skill_ops(out)["SAP"] == [_RAS]
    assert all(
        item.get("text") != "SAP" for ask in _asks(out) for item in ask.context["flagged"]
    )


def test_a_name_sentence_link_without_a_bullet_ask_raises_its_own_ask_with_that_employer():
    batch = [UpsertSkill(name="ISO 45001", category="domain", evidence=[_RAS])]
    out = enforce_attribution(batch, profile=_profile(), new_info=_ni(_TWO), source="interview")
    asks = _asks(out)
    assert len(asks) == 1
    assert asks[0].context["target"] == _RAS
    assert asks[0].context["anchor_employer"].startswith("Weberit")
    assert asks[0].context["flagged"] == [{"field": "skill_evidence", "text": "ISO 45001"}]


def test_a_link_from_a_sentence_the_model_split_is_kept():
    """The model partitioned the one sentence across both employers (rule a,
    #674 line 34): the sentence anchors nothing, for the bullets AND the links."""
    answer = (
        "Bei Weberit habe ich Shopfloor-Management eingeführt, Kaizen-Workshops habe ich "
        "schon in meiner Schichtleiterzeit moderiert."
    )
    batch = [
        AddBullets(target=_WEB, responsibilities=["Shopfloor-Management eingeführt."]),
        AddBullets(target=_RAS, responsibilities=["Kaizen-Workshops moderiert."]),
        UpsertSkill(name="Kaizen-Workshops", category="domain", evidence=[_RAS]),
    ]
    out = enforce_attribution(batch, profile=_profile(), new_info=_ni(answer), source="interview")
    assert _asks(out) == []
    assert _skill_ops(out) == {"Kaizen-Workshops": [_RAS]}


def test_a_name_in_two_sentences_naming_two_employers_holds_nothing():
    """delivery-q10 rec 182 (synthetic restatement): "PP" stands in a Rasselstein
    sentence AND a Weberit sentence; the link to either employer is right."""
    answer = (
        "Beim SAP-Rollout bei Rasselstein war ich Key-User für PP. "
        "Bei Weberit arbeite ich täglich mit PP und MM."
    )
    batch = [
        AddBullets(target=_RAS, responsibilities=["Beim SAP-Rollout als Key-User für PP tätig."]),
        AddBullets(target=_WEB, responsibilities=["Täglich mit PP und MM gearbeitet."]),
        UpsertSkill(name="PP", category="technical", evidence=[_RAS, _WEB]),
        # the captured name: "SAP" stands only in the Rasselstein sentence, "PP" in both
        UpsertSkill(name="SAP PP", category="technical", evidence=[_RAS, _WEB]),
    ]
    out = enforce_attribution(batch, profile=_profile(), new_info=_ni(answer), source="interview")
    assert _asks(out) == []
    assert _skill_ops(out) == {"PP": [_RAS, _WEB], "SAP PP": [_RAS, _WEB]}


def test_a_name_also_in_a_sentence_naming_no_employer_holds_nothing():
    answer = (
        "Bei Weberit habe ich die ISO-45001-Zertifizierung vorbereitet. "
        "ISO 45001 kenne ich auch aus meiner Zeit davor."
    )
    batch = [UpsertSkill(name="ISO 45001", category="domain", evidence=[_RAS])]
    out = enforce_attribution(batch, profile=_profile(), new_info=_ni(answer), source="interview")
    assert _asks(out) == []
    assert _skill_ops(out) == {"ISO 45001": [_RAS]}


def test_a_name_only_partly_in_a_sentence_holds_nothing():
    """One shared word ("Management") does not make the sentence the skill's."""
    answer = "Bei Weberit habe ich das Management-Reporting aufgebaut."
    batch = [UpsertSkill(name="Lean Management", category="domain", evidence=[_RAS])]
    out = enforce_attribution(batch, profile=_profile(), new_info=_ni(answer), source="interview")
    assert _asks(out) == []
    assert _skill_ops(out) == {"Lean Management": [_RAS]}


# ── fix round after adv/vault: F-6 (a link the vault holds) and F-5 (the
# scalars of an existing entry, set_field size/budget, a project's parent) ──

from applire.services.profile.reconcile.ops import SetField, UpsertProject, UpsertWork  # noqa: E402

_BUDGET = "Bei Weberit habe ich ein Budget von rund 6 Mio. € verantwortet und 38 Mitarbeitende geführt."
_MES = "Bei Weberit habe ich 2023 die Einführung eines MES-Systems an 14 Spritzgussmaschinen als Projektleiter verantwortet."


def test_f6_a_link_the_skill_already_holds_raises_no_question():
    profile = _profile()
    profile.skills = [Skill(name="SAP PP", category="tool", experience_refs=[_RAS])]
    answer = "Bei Weberit arbeite ich täglich mit SAP PP und MM, vor allem Disposition."
    out = enforce_attribution(
        [UpsertSkill(name="SAP PP", category="tool", evidence=[_WEB, _RAS]),
         AddBullets(target=_WEB, responsibilities=["Tägliche Arbeit mit SAP PP und MM (Disposition)."])],
        profile=profile, new_info=_ni(answer), source="interview")
    assert _asks(out) == [], "the stored Rasselstein link changes nothing: nothing to ask"
    assert _skill_ops(out) == {"SAP PP": [_WEB, _RAS]}


def test_f6_a_new_link_of_a_stored_skill_is_still_held():
    profile = _profile()
    profile.skills = [Skill(name="ISO 45001", category="domain", experience_refs=[])]
    out = enforce_attribution(_batch(), profile=profile, new_info=_ni(), source="interview")
    assert _skill_ops(out)["ISO 45001"] == []


def _scalar_asks(out):
    return [(i["field"], i["text"]) for a in _asks(out) for i in a.context["flagged"]]


def test_f5_upsert_work_on_an_existing_entry_holds_another_employers_figures():
    out = enforce_attribution(
        [UpsertWork(ref="x", target=_RAS, company="Rasselstein Umformtechnik GmbH",
                    budget_managed="ca. 6 Mio. €", team_size=38)],
        profile=_profile(), new_info=_ni(_BUDGET), source="interview")
    (op,) = [o for o in out if isinstance(o, UpsertWork)]
    assert op.team_size is None and op.budget_managed is None
    assert sorted(_scalar_asks(out)) == [("budget_managed", "ca. 6 Mio. €"), ("team_size", "38")]
    (ask,) = _asks(out)
    assert ask.context["anchor_employer"].startswith("Weberit")


def test_f5_the_right_entry_keeps_its_figures():
    out = enforce_attribution(
        [UpsertWork(ref="x", target=_WEB, company="Weberit Kunststofftechnik GmbH",
                    budget_managed="ca. 6 Mio. €", team_size=38)],
        profile=_profile(), new_info=_ni(_BUDGET), source="interview")
    assert _asks(out) == []
    (op,) = [o for o in out if isinstance(o, UpsertWork)]
    assert (op.team_size, op.budget_managed) == (38, "ca. 6 Mio. €")


def test_f5_a_filled_slot_is_not_asked_about_nothing_would_be_written():
    profile = _profile()
    profile.work_experience[1].team_size = 14
    out = enforce_attribution(
        [UpsertWork(ref="x", target=_RAS, company="Rasselstein Umformtechnik GmbH", team_size=38)],
        profile=profile, new_info=_ni(_BUDGET), source="interview")
    assert _asks(out) == []


def test_f5_set_field_size_and_budget_are_held():
    out = enforce_attribution(
        [SetField(target=_RAS, field="team_size", value="38"),
         SetField(target=_RAS, field="budget_managed", value="6 Mio. €")],
        profile=_profile(), new_info=_ni(_BUDGET), source="interview")
    assert not [o for o in out if isinstance(o, SetField)]
    assert sorted(_scalar_asks(out)) == [("budget_managed", "6 Mio. €"), ("team_size", "38")]
    assert len(_asks(out)) == 1, "one ask per entry"


def test_f5_a_project_parent_on_another_employer_is_held_and_move_places_it():
    profile = _profile()
    out = enforce_attribution(
        [UpsertProject(ref="p1", name="MES-Einführung", parent=_RAS, role="Projektleiter",
                       description="MES an 14 Spritzgussmaschinen")],
        profile=profile, new_info=_ni(_MES), source="interview")
    (proj,) = [o for o in out if isinstance(o, UpsertProject)]
    assert proj.parent is None, "the project is written; only its parent waits"
    (ask,) = _asks(out)
    assert ask.context["flagged"] == [{"field": "project_parent", "text": "MES-Einführung"}]
    written = apply_ops(profile, [proj], "interview").profile
    assert written.projects[0].associated_experience is None
    written.metadata = ProfileMetadata(pending_confirmations=[PendingConfirmation(
        confirmation_id="c-1", question=ask.question, options=list(ask.options),
        context=dict(ask.context), question_i18n=ask.question_i18n,
        options_i18n=ask.options_i18n, option_keys=list(ask.option_keys),
    )])
    moved = apply_ops(written, [ResolveConfirmation(confirmation_id="c-1", chosen_option=_option(ask, "move"))],
                      "manual_edit").profile
    assert moved.projects[0].associated_experience == _WEB


def test_f5_a_held_size_is_placed_into_an_empty_slot_only():
    profile = _profile()
    out = enforce_attribution([SetField(target=_RAS, field="team_size", value="38")],
                              profile=profile, new_info=_ni("Bei Weberit führe ich 38 Mitarbeitende."),
                              source="interview")
    (ask,) = _asks(out)
    profile.metadata = ProfileMetadata(pending_confirmations=[PendingConfirmation(
        confirmation_id="c-1", question=ask.question, options=list(ask.options),
        context=dict(ask.context), question_i18n=ask.question_i18n,
        options_i18n=ask.options_i18n, option_keys=list(ask.option_keys),
    )])
    profile.work_experience[0].team_size = 40
    result = apply_ops(profile, [ResolveConfirmation(confirmation_id="c-1", chosen_option=_option(ask, "move"))],
                       "manual_edit")
    assert result.profile.work_experience[0].team_size == 40, "an answered/stored size is never overwritten"
    assert any(n.label == "team_size: 38" for n in result.not_applied)
    profile.work_experience[0].team_size = None
    result = apply_ops(profile, [ResolveConfirmation(confirmation_id="c-1", chosen_option=_option(ask, "move"))],
                       "manual_edit")
    assert result.profile.work_experience[0].team_size == 38


def test_f5_a_client_named_in_a_relationship_sentence_holds_nothing():
    answer = "Bei Weberit war ich Kunde von Rasselstein und habe 38 Mitarbeitende geführt."
    out = enforce_attribution(
        [UpsertWork(ref="x", target=_WEB, company="Weberit Kunststofftechnik GmbH", team_size=38)],
        profile=_profile(), new_info=_ni(answer), source="interview")
    assert _asks(out) == []


def test_f5_a_value_whose_own_sentence_names_no_employer_holds_nothing_alone():
    """MD-Q11 shape (11 wrong holds in 530 Luna replays before this rule): the
    sentence the value came from names no employer, a later one names another."""
    answer = ("Für unseren Bereich habe ich 2022 die Bewertung langfristiger Aufträge eingeführt. "
              "Investitionsrechnungen habe ich bei Weberit gemacht.")
    out = enforce_attribution(
        [UpsertProject(ref="p1", name="Bewertung langfristiger Aufträge", parent=_RAS)],
        profile=_profile(), new_info=_ni(answer), source="interview")
    assert _asks(out) == []
    assert [o.parent for o in out if isinstance(o, UpsertProject)] == [_RAS]


def test_f5_a_silent_sibling_follows_the_held_one():
    """``… rund 6 Mio. € verantwortet und 38 Mitarbeitende geführt.`` splits at
    "Mio.": the 38's sentence names nobody, the budget's names Weberit."""
    out = enforce_attribution(
        [SetField(target=_RAS, field="team_size", value="38")],
        profile=_profile(), new_info=_ni(_BUDGET), source="interview")
    assert _asks(out) == [], "alone, a silent sentence holds nothing"
