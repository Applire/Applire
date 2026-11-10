# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""790:62 + 783:247 (Hazelnut build 8) — the literal coverage corpus carries only
what the vault states.

`profile_literal_corpus` answers "does the vault literally carry this term"; a
yes stops the `present_unsupported` guards (CV, letter) and the pre-download diff
from asking. Two channels said yes for text the vault does not state:

* 790:62 — the add receipt of a skill the person REMOVED still named it
  (only receipts of entries present-but-unclaimable were dropped);
* 783:247 — system bookkeeping (a parked conflict's UNACCEPTED incoming value,
  aspiration/identity verdicts, kept-separate pairs) flattened into the corpus,
  although no writer ever sees it (`prompt_view.PROMPT_EXCLUDED_METADATA_KEYS`).

Both directions are pinned: a re-added skill, a merged spelling and an accepted
write's receipt still ground. Captured data (19 synthetic vaults, 1,828
vault×term pairs): 0 presence moves — neither shape occurs there; the defect is
the adversarial probe's (build 2 r3). Synthetic data only.
"""
from __future__ import annotations

import copy

from applire.services.ats_audit import surface_present
from applire.services.cv_diff import compute_cv_profile_diff
from applire.services.keyword_ledger import profile_literal_corpus


def _vault() -> dict:
    return {
        "personal_info": {"name": "Jonas Weidner"},
        "skills": [{"id": "s-py", "name": "Python", "status": "confirmed"}],
        "work_experience": [{
            "id": "w1", "company": "Kranich Daten GmbH", "title": "Backend-Entwickler",
            "achievements": ["REST-APIs in Python für die Tourenplanung gebaut."],
        }],
        "metadata": {"enrichment_history": [
            {"source": "interview", "changes": [
                {"section": "skills", "field": "Terraform", "action": "added",
                 "old_value": None, "new_value": {"id": "s-tf", "name": "Terraform", "status": "confirmed"},
                 "rationale": "Added Terraform in skills."},
                {"section": "skills", "field": "Python", "action": "added",
                 "old_value": None, "new_value": {"id": "s-py", "name": "Python", "status": "confirmed"},
                 "rationale": "Added Python in skills."},
            ]},
            {"source": "manual_edit", "changes": [
                {"section": "skills", "field": "Terraform", "action": "removed",
                 "old_value": {"id": "s-tf", "name": "Terraform", "status": "confirmed"}, "new_value": None,
                 "rationale": "Removed Terraform in skills (manual section edit)."},
            ]},
        ]},
    }


def _present(term: str, vault: dict) -> bool:
    return surface_present(term, profile_literal_corpus(vault))


# ── 790:62 — a removed entry's receipts ─────────────────────────────────────

def test_a_removed_skill_is_no_longer_literally_in_the_vault():
    assert not _present("Terraform", _vault())


def test_a_kept_skill_still_grounds_through_its_receipt_and_its_entry():
    assert _present("Python", _vault())


def test_a_skill_re_added_under_the_same_name_grounds_again():
    v = _vault()
    v["skills"].append({"id": "s-tf2", "name": "Terraform", "status": "confirmed"})
    assert _present("Terraform", v)


def test_a_renamed_skills_former_name_still_grounds_through_its_receipt():
    """Only a REMOVAL drops receipts: an update receipt's old name is the
    person's own earlier word for an entry the vault still carries."""
    v = _vault()
    v["skills"].append({"id": "s-k8", "name": "Kubernetes", "status": "confirmed"})
    v["metadata"]["enrichment_history"].append({"source": "manual_edit", "changes": [
        {"section": "skills", "field": "Kubernetes", "action": "updated",
         "old_value": {"id": "s-k8", "name": "K8s", "status": "confirmed"},
         "new_value": {"id": "s-k8", "name": "Kubernetes", "status": "confirmed"},
         "rationale": "Updated Kubernetes in skills (manual section edit)."},
    ]})
    assert _present("K8s", v)


def test_a_removed_alias_string_is_not_a_removed_entry():
    v = _vault()
    v["metadata"]["enrichment_history"].append({"source": "match_separated", "changes": [
        {"section": "skills", "field": "aliases", "action": "removed",
         "old_value": "Python", "new_value": None, "rationale": "Removed 'Python' as another name."},
    ]})
    assert _present("Python", v)
    v["skills"] = []
    v["work_experience"] = []
    assert _present("Python", v)  # its add receipt still grounds it: no entry was removed


def test_the_removed_skill_stays_present_when_the_vault_states_it_elsewhere():
    v = _vault()
    v["work_experience"][0]["achievements"].append("Infrastruktur mit Terraform provisioniert.")
    assert _present("Terraform", v)


def test_the_pre_download_diff_now_asks_about_the_removed_skill():
    """Seam: `cv_diff.compute_cv_profile_diff` (the pre-download notice)."""
    changes = compute_cv_profile_diff({"skills": ["Terraform", "Python"]}, _vault())
    assert [c.new_value for c in changes] == ["Terraform"]


# ── 783:247 — system bookkeeping ────────────────────────────────────────────

def _with_conflict(v: dict) -> dict:
    v = copy.deepcopy(v)
    v["metadata"]["pending_conflicts"] = [{
        "conflict_id": "c1", "section": "skills", "entity_id": "s-py", "field": "name",
        "existing_value": "Python", "incoming_value": "Kubernetes", "source": "testimony",
        "resolved": False,
    }]
    v["metadata"]["aspiration_verdicts"] = [{
        "key": "k1", "text": "Ziel: Flottenplattform auf Rust migrieren.", "section": "work_experience",
        "entry_id": "w1", "aspiration": True,
    }]
    v["metadata"]["separate_entry_pairs"] = [["Snowflake", "Python"]]
    v["metadata"]["entry_identity_verdicts"] = [{"a": "Airflow", "b": "Python", "same": False}]
    return v


def test_a_parked_conflicts_incoming_value_is_not_vault_text():
    assert not _present("Kubernetes", _with_conflict(_vault()))


def test_verdicts_and_kept_separate_pairs_are_not_vault_text():
    v = _with_conflict(_vault())
    assert not _present("Rust", v)
    assert not _present("Snowflake", v)
    assert not _present("Airflow", v)


def test_the_vaults_own_content_still_grounds_beside_the_bookkeeping():
    v = _with_conflict(_vault())
    assert _present("Python", v)
    assert _present("Tourenplanung", v)


def test_the_input_is_not_mutated():
    v = _with_conflict(_vault())
    before = copy.deepcopy(v)
    profile_literal_corpus(v)
    assert v == before
