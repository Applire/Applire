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

"""#480 PR 4 / ADR-059 amended 2026-08-09 §7.5(a) — `denial_release_corpus`.

What may RELEASE a persisted denial narrows to **attested entity labels**.
`profile_literal_corpus` flattens the whole vault, so a sentence typed into the
CV section editor (`work_experience[].responsibilities`) functioned as the
"independent affirmation" that lifted the candidate's own recorded denial —
#480's second probe half.

Two things this file pins:

1. **The corpus's membership** — which vault fields are attested enough to
   release a denial, and which are deliberately out (prose of every kind).
2. **The five-site lockstep.** ADR-059's #486 amendment clause (b) documents a
   divergence that was *already real* when four of five copies of a rule were
   updated. The pin here is structural, not a grep: each of the five call paths
   into the floor/release predicate is invoked with a spy substituted for
   `denial_release_corpus`, and the spy must fire. A site that reverts to
   `profile_literal_corpus` never calls it, and its own test fails — one
   sentinel per site, which IS the lockstep evidence.
"""

import pytest

from applire.services.keyword_ledger import profile_literal_corpus
from applire.services.profile.reconcile.stance import denial_release_corpus

# A vault whose ONLY mention of "Kubernetes" outside the skills list is a
# hand-typed bullet — the #480 edited-bullet shape, kept synthetic.
_VAULT = {
    "personal_info": {"full_name": "Daniel Kovač", "summary": "Kafka everywhere."},
    "professional_summary": {"headline": "Terraform specialist"},
    "work_experience": [
        {
            "id": "w1",
            "company": "Rheinwerk GmbH",
            "role": "Automation Engineer",
            "responsibilities": ["Ran the Kubernetes rollout for two teams."],
            "achievements": ["Cut Grafana alert noise by half."],
            "technologies": ["Prometheus"],
        }
    ],
    "skills": [
        {"name": "Ansible", "status": "confirmed"},
        {"name": "Helm", "status": "unconfirmed"},
        {"name": "Docker", "status": "denied"},
    ],
    "certifications": [
        {"name": "CKA", "status": "confirmed"},
        {"name": "CKAD", "status": "unconfirmed"},
    ],
    "languages": [
        {"language": "Slovenian", "status": "confirmed"},
        {"language": "Norwegian", "status": "unconfirmed"},
    ],
    "education": [{"institution": "TU Graz", "field_of_study": "Kubernetes theory"}],
    # ADR-059 amended 2026-10-22 (E-11): the trail says who wrote each bullet. The
    # Kubernetes bullet was typed into the CV section editor (#480 — `manual_edit`
    # receipts the whole entry before and after); the Grafana achievement came
    # from an interview turn. Neither may release a denial.
    "metadata": {
        "enrichment_history": [
            {
                "source": "manual_edit",
                "changes": [
                    {
                        "section": "work_experience",
                        "field": "Automation Engineer @ Rheinwerk GmbH",
                        "action": "updated",
                        "old_value": {"id": "w1", "responsibilities": []},
                        "new_value": {
                            "id": "w1",
                            "responsibilities": ["Ran the Kubernetes rollout for two teams."],
                        },
                    }
                ],
            },
            {
                "source": "interview",
                "changes": [
                    {
                        "section": "work_experience",
                        "field": "achievements",
                        "action": "merged",
                        "old_value": None,
                        "new_value": ["Cut Grafana alert noise by half."],
                    }
                ],
            },
        ]
    },
}


def _corpus() -> str:
    return denial_release_corpus(_VAULT)


# ── Membership: what IS attested ─────────────────────────────────────────────


def test_a_claimable_skill_name_is_in_the_corpus():
    assert "ansible" in _corpus()


def test_a_confirmed_certification_name_is_in_the_corpus():
    assert "cka" in _corpus()


def test_a_confirmed_language_is_in_the_corpus():
    assert "slovenian" in _corpus()


def test_work_role_and_company_are_in_the_corpus():
    """The corpus's weakest members, kept per the PO ruling — `WorkEntry` has
    no status field, so they are only as attested as the reviewed import that
    wrote them (ADR-059 amended 2026-08-09 clause 1's recorded caveat)."""
    corpus = _corpus()
    assert "automation engineer" in corpus
    assert "rheinwerk" in corpus


def test_a_work_technologies_tag_is_in_the_corpus():
    """PO addendum to the 2026-08-09 amendment, ruled the same day: a
    structured tag list is a vault ENTITY label of the same trust level as
    role/company, not prose. Excluding it silently narrowed the charter-run-4
    guarantee #249 pinned and re-opened a slice of #207's over-blocking class.
    Same weakest-member caveat: `WorkEntry` carries no status field."""
    assert "prometheus" in _corpus()


# ── Membership: what is deliberately OUT ─────────────────────────────────────


def test_an_unconfirmed_skill_never_reaches_the_release_corpus():
    """Step 1's `exclude_unconfirmed` wrap, subsumed by construction: the
    shared `_UNCLAIMABLE_STATUSES` predicate excludes `unconfirmed` AND
    `denied` in one place."""
    assert "helm" not in _corpus()


def test_a_denied_skill_never_reaches_the_release_corpus():
    assert "docker" not in _corpus()


def test_an_unconfirmed_certification_is_not_attested():
    assert "ckad" not in _corpus()


def test_an_unconfirmed_language_is_not_attested():
    assert "norwegian" not in _corpus()


def test_hand_typed_bullet_prose_is_never_in_the_release_corpus():
    """THE #480 edited-bullet vector: the only "Kubernetes" here is a bullet
    the candidate typed into the CV section editor. It may not release a
    persisted denial."""
    assert "kubernetes" not in _corpus()


def test_prose_is_out_which_is_the_line_the_corpus_actually_draws():
    corpus = _corpus()
    assert "grafana" not in corpus, "an interview-written achievement is not the candidate's document"
    assert "kafka" not in corpus, "a personal-info summary is prose"
    assert "terraform" not in corpus, "a professional summary is prose"
    assert "tu graz" not in corpus, "education free text is prose"


# ── Shape and tolerance ──────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", [None, {}, "nonsense", 17, []])
def test_malformed_input_yields_an_empty_corpus(bad):
    assert denial_release_corpus(bad) == ""


def test_malformed_entries_are_skipped_not_raised():
    out = denial_release_corpus(
        {
            "skills": ["Bare Legacy String", {"no_name": 1}, None],
            "certifications": ["oops", {"name": None}],
            "languages": [None, {"language": ""}],
            "work_experience": ["oops", {"role": None, "company": 3}],
        }
    )
    assert "bare legacy string" in out


def test_the_corpus_is_normalised_like_every_other_presence_corpus():
    """Same `ats_audit._norm` the ledger's presence predicates read, or the
    matcher and the corpus would disagree about what a token even is."""
    out = denial_release_corpus({"skills": [{"name": "Code-Review Tooling"}]})
    assert "code review tooling" in out


def test_labels_are_separated_so_no_phrase_spans_two_entities():
    """Joining labels with a bare space would let two unrelated entities read
    as one phrase ("Machine" + "Learning" → "machine learning"), manufacturing
    an affirmation nobody attested. Fail-closed: the separator is a word
    boundary the presence predicates cannot match across."""
    out = denial_release_corpus(
        {"skills": [{"name": "Machine"}, {"name": "Learning"}]}
    )
    assert "machine learning" not in out


def test_the_corpus_is_pure():
    before = str(_VAULT)
    denial_release_corpus(_VAULT)
    assert str(_VAULT) == before


# ── The coverage consumers stay exactly as they were ─────────────────────────


def test_profile_literal_corpus_sees_prose_but_no_unclaimable_entry():
    """`profile_literal_corpus` answers a COVERAGE question (ATS
    `present_unsupported`, the cover-letter guard, the US147 pre-download
    diff), not a release question, so it keeps the vault's prose (ADR-059
    amended 2026-08-09 clause 2). Build-2 adversarial F-A (ruling R3-a, R3-1
    precedent ADR-061 cl. 3): it no longer carries an `unconfirmed`/`denied`
    entry — such a row backs nothing, so it may not decide that a keyword is
    supported (the Oracle's index reads the same claimable vault)."""
    corpus = profile_literal_corpus(_VAULT)
    assert "kubernetes" in corpus
    assert "grafana" in corpus
    assert "ansible" in corpus and "cka" in corpus and "slovenian" in corpus
    for unclaimable in ("helm", "docker", "ckad", "norwegian"):
        assert unclaimable not in corpus, unclaimable


# ── The five-site lockstep pin ───────────────────────────────────────────────


class _Spy:
    """Substitute for `denial_release_corpus` that records that it ran."""

    def __init__(self, returns: str = ""):
        self.calls: list = []
        self._returns = returns

    def __call__(self, profile_json):
        self.calls.append(profile_json)
        return self._returns


_LEDGER_ROW = {
    "concept": "CSS",
    "surface_forms": ["CSS"],
    "sources": ["required"],
    "fit_weight": 1.0,
    "status": "gap",
    "evidence": "",
    "claimable": False,
}


def test_site_1_build_keyword_ledger_uses_the_release_corpus(monkeypatch):
    from applire.services import keyword_ledger as kl

    spy = _Spy()
    monkeypatch.setattr(kl, "denial_release_corpus", spy)
    kl.build_keyword_ledger(
        [{"concept": "CSS", "status": "direct", "evidence": "x"}],
        ["CSS"],
        [],
        [],
        denied_concepts=["Tailwind CSS"],
        profile_json=_VAULT,
    )
    assert spy.calls, "build_keyword_ledger's vault corpus must be the release corpus"


def test_site_2_reevaluate_gap_ledger_uses_the_release_corpus(monkeypatch):
    from applire.services import keyword_ledger as kl

    spy = _Spy()
    monkeypatch.setattr(kl, "denial_release_corpus", spy)
    kl.reevaluate_gap_ledger_against_vault([dict(_LEDGER_ROW)], _VAULT)
    assert spy.calls, "the strip thread's DENIAL check must read the release corpus"


def test_site_3_assert_claimable_backed_uses_the_release_corpus(monkeypatch):
    from applire.services import keyword_ledger as kl

    spy = _Spy()
    monkeypatch.setattr(kl, "denial_release_corpus", spy)
    kl.assert_claimable_backed(
        [dict(_LEDGER_ROW, status="direct", claimable=True, evidence="x")],
        _VAULT,
        seam="test",
    )
    assert spy.calls, (
        "the heal corpus is the FIFTH path into `_independently_affirmed` — "
        "leaving it on `profile_literal_corpus` recreates exactly the "
        "divergence ADR-059's #486 clause (b) documents"
    )


def test_site_4_the_session_upgrade_seam_cannot_reach_the_wide_corpus():
    """`services/session.py`'s upgrade-seam thread. Its call site sits inside an
    async interview turn against a persisted `GapAnalysis`, so the pin here is
    structural rather than a spy: the module binds the release corpus, and the
    wide corpus is **not bound in the module at all** — the floor site was its
    only importer, so divergence would need a re-import a reviewer sees."""
    import inspect

    from applire.services import session as session_mod

    assert session_mod.denial_release_corpus is denial_release_corpus
    assert not hasattr(session_mod, "profile_literal_corpus")
    src = inspect.getsource(session_mod._upgrade_ledger_for_addressed_gap)
    assert "denial_release_corpus(" in src


def test_site_5_the_agent_bridge_thread_cannot_reach_the_wide_corpus():
    import inspect

    from applire.services.profile.reconcile import agent_bridge

    assert agent_bridge.denial_release_corpus is denial_release_corpus
    assert not hasattr(agent_bridge, "profile_literal_corpus")
    src = inspect.getsource(agent_bridge.submit_agent_claims)
    assert "denial_release_corpus(" in src


# ── ADR-059 amended 2026-10-22 (E-11, ruling G-2): imported prose releases ────


def _with_bullets(history: list[dict]) -> dict:
    return {
        "work_experience": [
            {
                "id": "w1",
                "company": "Weberit GmbH",
                "role": "Produktionsleiter",
                "responsibilities": [
                    "Verantwortung für die Produktion in zwei Fertigungsbereichen.",
                    "OEE im Spritzguss von 61 % auf 73 % gesteigert.",
                ],
            }
        ],
        "projects": [{"name": "MES", "description": "Pilotlinie für Maschinendatenerfassung."}],
        "signature_stories": [{"title": "Kaizen", "outcome": "Rüstzeit halbiert mit SMED."}],
        "professional_summary": {"de": "Lean-Experte mit Reanimationserfahrung."},
        "metadata": {"enrichment_history": history},
    }


def test_an_imported_bullet_releases_the_bare_term():
    """The build-2 reprobe shape: "Produktion" floored for a Produktionsleiter whose
    own CV bullet names it, under the narrower denial "direkte Produktion für
    Lebensmittelkunden". An unrecorded bullet is the initial import's."""
    corpus = denial_release_corpus(_with_bullets([]))
    assert "produktion" in corpus
    assert "maschinendatenerfassung" in corpus, "a project description is a bullet too"


def test_an_import_record_keeps_its_bullets_releasing():
    history = [{"source": "cv_upload", "changes": [{
        "section": "work_experience", "field": "responsibilities", "action": "merged",
        "old_value": None, "new_value": ["OEE im Spritzguss von 61 % auf 73 % gesteigert."]}]}]
    assert "oee" in denial_release_corpus(_with_bullets(history))


def test_an_interview_written_bullet_never_releases():
    history = [{"source": "interview", "changes": [{
        "section": "work_experience", "field": "responsibilities", "action": "merged",
        "old_value": None, "new_value": ["OEE im Spritzguss von 61 % auf 73 % gesteigert."]}]}]
    corpus = denial_release_corpus(_with_bullets(history))
    assert "oee" not in corpus
    assert "zwei fertigungsbereichen" in corpus, "only the interview's own text is excluded"


@pytest.mark.parametrize("source", ["agent_interview", "testimony", "manual_edit", "manual_role_add", "something_new"])
def test_every_non_import_source_is_excluded_fail_closed(source):
    history = [{"source": source, "changes": [{
        "section": "work_experience", "field": "responsibilities", "action": "merged",
        "old_value": None, "new_value": ["OEE im Spritzguss von 61 % auf 73 % gesteigert."]}]}]
    assert "oee" not in denial_release_corpus(_with_bullets(history))


def test_an_editor_change_excludes_only_the_text_it_added():
    """`manual_edit` receipts the whole entry before and after; the bullets that
    were already there stay the import's."""
    entry_before = {"id": "w1", "responsibilities": ["Verantwortung für die Produktion in zwei Fertigungsbereichen."]}
    entry_after = {"id": "w1", "responsibilities": [
        "Verantwortung für die Produktion in zwei Fertigungsbereichen.",
        "OEE im Spritzguss von 61 % auf 73 % gesteigert."]}
    history = [{"source": "manual_edit", "changes": [{
        "section": "work_experience", "field": "Produktionsleiter @ Weberit GmbH", "action": "updated",
        "old_value": entry_before, "new_value": entry_after}]}]
    corpus = denial_release_corpus(_with_bullets(history))
    assert "oee" not in corpus
    assert "zwei fertigungsbereichen" in corpus, "the bullet that was already there stays the import's"


def test_stories_and_summary_never_release():
    corpus = denial_release_corpus(_with_bullets([]))
    assert "smed" not in corpus, "signature stories are interview material"
    assert "reanimation" not in corpus, "the summary can be system-seeded"


def test_no_phrase_spans_two_bullets():
    corpus = denial_release_corpus(_with_bullets([]))
    assert "fertigungsbereichen. | oee" in corpus, "each bullet sits behind the label separator"


# ── E-11 end to end through the real floor (no spy) ──────────────────────────

_REPROBE_DENIAL = [{"concept": "direkte Produktion für Lebensmittelkunden", "level": "direct"}]


def _floor_status(history: list[dict]) -> dict:
    from applire.services.keyword_ledger import build_keyword_ledger

    profile = _with_bullets(history)
    profile["metadata"]["denied_concepts"] = _REPROBE_DENIAL
    ledger = build_keyword_ledger(
        [{"concept": "Produktion", "status": "direct",
          "evidence": "Verantwortung für die Produktion in zwei Fertigungsbereichen."}],
        ["Produktion"], [], [],
        denied_concepts=_REPROBE_DENIAL,
        profile_json=profile,
    )
    return next(e for e in ledger if e["concept"] == "Produktion")


def test_build_keyword_ledger_releases_produktion_on_the_imported_bullet():
    """The build-2 reprobe (R2-2 cl. 3): a Produktionsleiter's bare "Produktion"
    under "direkte Produktion für Lebensmittelkunden" — released by his own CV."""
    row = _floor_status([])
    assert row["status"] == "direct" and row["claimable"] is True, row


def test_build_keyword_ledger_still_floors_when_only_an_interview_wrote_it():
    history = [{"source": "interview", "changes": [{
        "section": "work_experience", "field": "responsibilities", "action": "merged",
        "old_value": None,
        "new_value": ["Verantwortung für die Produktion in zwei Fertigungsbereichen."]}]}]
    row = _floor_status(history)
    assert row["status"] == "gap" and row["claimable"] is False, row
