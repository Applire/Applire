# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet WP-Q3, RULING R-2 / Q3-1 as amended by MD-Q9 R2: a role title recorded
in ONE form is the writer's translation; a role with several recorded names
prints one of them, in every document (no language is read from a title or from
prose). The VAULT FIELDS block states the one-form fact in a cross-language
document. Employer, degree and institution keep the check.

Bench 2026-11-15 (base 956bad2f, luna, operations_marcus_de.cross_de2en): the
writer translated every title in round 1, and the recorded-name finding had the
terminal corrector revert them all. Delivery 1b (Cross): the writer translated,
later rounds re-picked the German recorded names."""
from __future__ import annotations

from applire.services.cv_v2 import ComposeNotes, V2CheckContext, V2Terminal, compose_v2

_VAULT = {
    "personal_info": {"name": "Marcus"},
    "work_experience": [{
        "id": "w1", "company": "Weberit Kunststofftechnik GmbH", "role": "Produktionsleiter",
        "role_aliases": ["Projektleiter"], "start_date": "2019-01", "end_date": None,
        "responsibilities": ["Leitung der Serienfertigung mit drei Schichten und 60 Mitarbeitenden.",
                             "Begleitung der jährlichen ISO-9001-Audits als Bereichsverantwortlicher.",
                             # MD-Q7 F1: the alias is used in the candidate's own German
                             # sentence — the record says it is German (marcus shape).
                             "2023 als Projektleiter die Einführung eines MES-Systems verantwortet."],
    }],
    "education": [{"id": "e1", "degree": "Industriemeister Metall (IHK)", "institution": "IHK"}],
    "skills": [],
}
_PROSE = {
    "summary": "Production leader.",
    "work": [{"id": "w1", "role": "Production Manager", "company": "Weberit Plastics",
              "bullets": ["Led series production across three shifts."], "projects": []}],
    "education": [{"id": "e1", "degree": "Master craftsman in metalworking", "institution": "IHK"}],
}


def _compose(language):
    notes = ComposeNotes()
    out = compose_v2(_PROSE, _VAULT, notes=notes, document_language=language)
    return out, notes


def test_a_title_with_several_recorded_names_prints_one_of_them_md_q9_r2():
    """MD-Q9 R2: "Produktionsleiter" + alias "Projektleiter" — the EN document's
    "Production Manager" is neither recorded name: the stored name and the finding,
    exactly as in a same-language document."""
    out, notes = _compose("en")
    assert out.work_history[0].role == "Produktionsleiter"
    assert [n[0] for n in notes.names_not_recorded if n[0] == "work w1 role"] == ["work w1 role"]


def test_a_title_recorded_in_one_form_is_the_writers_translation():
    vault = {**_VAULT, "work_experience": [{**_VAULT["work_experience"][0], "role_aliases": []}]}
    notes = ComposeNotes()
    out = compose_v2(_PROSE, vault, notes=notes, document_language="en")
    assert out.work_history[0].role == "Production Manager"
    assert not [n for n in notes.names_not_recorded if n[0] == "work w1 role"]


def test_the_same_language_keeps_the_recorded_name_check():
    out, notes = _compose("de")
    assert out.work_history[0].role == "Produktionsleiter"
    assert [n[0] for n in notes.names_not_recorded if n[0] == "work w1 role"] == ["work w1 role"]


def test_a_one_form_title_keeps_the_check_in_the_vaults_own_language():
    """The waiver is for a translation. In a document in the vault's own language a
    title the vault does not record ("Werkleiter" for "Produktionsleiter") is not one:
    the stored title prints and the finding is raised, also when the title has no alias."""
    vault = {**_VAULT, "work_experience": [{**_VAULT["work_experience"][0], "role_aliases": []}]}
    prose = {**_PROSE, "work": [{**_PROSE["work"][0], "role": "Werkleiter",
                                 "company": "Weberit Kunststofftechnik GmbH"}]}
    for language in ("de", None):
        notes = ComposeNotes()
        out = compose_v2(prose, vault, notes=notes, document_language=language)
        assert out.work_history[0].role == "Produktionsleiter"
        assert [n[0] for n in notes.names_not_recorded if n[0] == "work w1 role"] == ["work w1 role"]


def test_employer_and_degree_keep_the_check_across_languages():
    out, notes = _compose("en")
    assert out.work_history[0].company == "Weberit Kunststofftechnik GmbH"
    assert out.education[0].degree == "Industriemeister Metall (IHK)"
    locs = {n[0] for n in notes.names_not_recorded}
    assert {"work w1 company", "education e1 degree"} <= locs


def test_the_terminal_compose_passes_the_document_language():
    """Seam: V2Terminal.compose (every terminal round) hands ctx.language on."""
    vault = {**_VAULT, "work_experience": [{**_VAULT["work_experience"][0], "role_aliases": []}]}
    t = V2Terminal(ctx=V2CheckContext(profile_json=vault, keyword_ledger=[], job_dict={}, language="en"))
    out = t.compose(_PROSE, vault, None)
    assert out.work_history[0].role == "Production Manager"


def test_the_language_round_compose_passes_the_document_language():
    """Seam: the language-round compose in services/cv.py passes document_language."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "backend/applire/services/cv.py").read_text()
    i = src.index("def _compose_for_language")
    assert "document_language=document_language" in src[i:i + 600]


# ── MD-Q7 F1 / MD-Q9 R2 (adversarial 1b, probes p3.py and r1.py) ─────────────
_DE = ["Verantwortlich für die Fertigungsplanung und die Leitung der Schichtteams im gesamten Werk.",
       "Einführung des neuen MES gemeinsam mit der Qualität und der IT."]
_EN = ["Responsible for the production planning and the management of the shift teams across the plant.",
       "Introduced the new MES together with the quality department and the IT team."]


def _probe(role, aliases, writer_role, lang, prose=_DE):
    vault = {"personal_info": {"name": "M"}, "work_experience": [{
        "id": "w1", "company": "Weberit", "role": role, "role_aliases": aliases,
        "start_date": "2019-01", "responsibilities": prose}], "skills": [], "education": []}
    draft = {"summary": "x", "work": [{"id": "w1", "role": writer_role, "bullets": ["Led shift planning."],
                                       "projects": []}], "skills": []}
    notes = ComposeNotes()
    out = compose_v2(draft, vault, notes=notes, document_language=lang)
    return out.work_history[0].role, [n[0] for n in notes.names_not_recorded]


def test_a_recorded_alias_keeps_the_check_md_q7_f1():
    """p3.py: "Production Manager" is recorded; "Head of Production" is neither name."""
    for lang in ("en", "de"):
        assert _probe("Produktionsleiter", ["Production Manager"], "Head of Production", lang) == (
            "Produktionsleiter", ["work w1 role"])


def test_the_recorded_document_language_form_is_accepted_md_q7_f1():
    assert _probe("Produktionsleiter", ["Production Manager"], "Production Manager", "en") == (
        "Production Manager", [])


# r1.py, the reviewer's inputs: the outcome follows the number of recorded names,
# never the language of the title or of the entry's prose.
_R1 = [
    ("Head of Controlling / Leiter Controlling", [], True),   # one stored form = one name
    ("Teamleader", [], True),
    ("Projektleiter", [], True),
    ("Controller", [], True),
    ("Marketing Manager", [], True),
    ("Sachbearbeiter", ["Clerk"], False),
    ("Leiter Controlling", ["Head of Controlling"], False),
    ("Teamleiter", ["Team Lead"], False),
]


def test_the_rule_reads_no_language_md_q9_r2():
    from applire.services.cv_v2 import role_title_is_translated

    for role, aliases, one_form in _R1:
        e = {"id": "w1", "role": role, "role_aliases": aliases}
        for prose in (_DE, _EN):
            for doc in ("de", "en"):
                v = {"work_experience": [{**e, "responsibilities": prose}]}
                assert role_title_is_translated(e, v, doc) is one_form, (role, aliases, doc)


def test_several_recorded_names_enforce_in_either_prose_language_md_q9_r2():
    """r1.py: "Leiter Controlling" + "Head of Controlling" in English prose, German
    document — the old reader judged both English and waived the check."""
    for prose in (_DE, _EN):
        for doc in ("de", "en"):
            assert _probe("Leiter Controlling", ["Head of Controlling"], "Head of Finance", doc, prose) == (
                "Leiter Controlling", ["work w1 role"])


# ── Delivery 1b (Cross, rec 241/256): every round is told the translation fact ──
def test_the_vault_fields_block_states_the_translation_fact_cross_language_only():
    """Delivery 1b (Cross, ba8b1d7c): the writer translated all three titles, then a
    correction round re-picked the German recorded names and every later round kept
    them — the block said only "recorded names …". Replayed on luna (rec 256, the
    first terminal corrector): 0/3 English titles as captured, 3/3 with this note."""
    import json
    from pathlib import Path

    from applire.services.cv_v2 import build_v2_fields_block_for

    vault = json.loads((Path(__file__).resolve().parents[1]
                        / "files/quality_bench/operations_marcus_de/cross_de2en/vault.json").read_text())
    vault = vault.get("profile") or vault
    en = build_v2_fields_block_for(vault, {}, [], "en")
    de = build_v2_fields_block_for(vault, {}, [], "de")
    note = "recorded in one form only"
    roles_en = [line for line in en.splitlines() if "  - work [" in line]
    # MD-Q9 R2: only the one-form title is marked (w2 "Fertigungsmeister /
    # Schichtleiter"); w1 and w3 have an alias and print a recorded name
    assert [note in line for line in roles_en] == [False, True, False]
    assert note not in de  # a same-language prompt is unchanged
    assert de == build_v2_fields_block_for(vault, {}, [])


def test_a_role_with_an_alias_is_not_marked_for_translation():
    from applire.services.cv_v2 import build_v2_fields_block_for

    vault = {"work_experience": [{"id": "w1", "role": "Produktionsleiter", "role_aliases": ["Production Manager"],
                                  "company": "W", "responsibilities": _DE}], "skills": []}
    assert "recorded in one form only" not in build_v2_fields_block_for(vault, {}, [], "en")


def test_the_generation_seam_passes_the_document_language_to_the_block():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "backend/applire/services/cv.py").read_text()
    i = src.index("v2_fields_block = build_v2_fields_block_for(")
    assert "document_language" in src[i:i + 200]
