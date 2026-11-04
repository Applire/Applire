# Copyright (C) 2026 Tobias Rosenbaum
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

"""ADR-100 — code checks facts, the model judges with a verbatim quote.

The affirmative floor (``assert_claimable_backed``, ADR-061 #318) used to veto a
claimable ledger row whenever ``ground_skill_claim`` could not find the
REQUIREMENT's words in the vault. A German vault answering an English posting
therefore lost every requirement the classifier had correctly judged met (founder
UAT-3, 2026-11-04: five interview-answered concepts → "C — Lücken").

Now the literal arm is a cheap POSITIVE only, and a miss falls through to the
classifier's own verbatim vault span (``backing.quote``), checked by ONE Fact
(``citation.quote_in_vault``). All fixtures here are synthetic: a fictional lab-IT
lead at fictional employers (the LIMS twin of ``Runs/Hazelnut/fact-judgment``).
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from applire.services.citation import quote_in_vault
from applire.services.keyword_ledger import (
    UNBACKED_NO_VERIFIABLE_QUOTE,
    assert_claimable_backed,
    build_keyword_ledger,
    downgrade_ledger_for_concepts,
    reevaluate_gap_ledger_against_vault,
    refresh_persist_and_rescore,
    render_ledger_prompt_block,
    render_ledger_reviewer_block,
    upgrade_ledger_for_concepts,
)

ERP_QUOTE = (
    "Anbindung des Probenannahme-LIMS und des Qualitätskontroll-LIMS an das "
    "SAP-S/4HANA-System über eine zentrale Schnittstellenplattform"
)
API_QUOTE = "Erarbeitung der Schnittstellenstrategie: REST-Schnittstellen statt Dateiaustausch"

#: A German vault; the posting (and so every ledger concept below) is English.
VAULT = {
    "personal_info": {"name": "Lena Marquardt", "location": "Bremen"},
    "work_experience": [
        {
            "id": "w1",
            "role": "Leiterin Labor-IT",
            "company": "Veltrup Diagnostik GmbH",
            "start_date": "2019-04",
            "is_current": True,
            "responsibilities": [
                ERP_QUOTE + "; Auftragsdaten fließen seitdem ohne Doppelerfassung.",
                API_QUOTE + ", Versionierung und ein Katalog aller Systemschnittstellen.",
            ],
            "achievements": [],
            "technologies": ["Python"],
        }
    ],
    "skills": [
        {"name": "Python", "status": "confirmed"},
        {"name": "Teamführung", "status": "confirmed"},
        {"name": "Kubernetes-Betrieb im Rechenzentrum", "status": "unconfirmed"},
    ],
    "languages": [{"language": "Deutsch", "level": "Muttersprache", "status": "confirmed"}],
}


def _row(concept: str, quote: str | None = None, status: str = "direct") -> dict:
    row = {
        "concept": concept,
        "surface_forms": [concept],
        "sources": ["required"],
        "fit_weight": 1.0,
        "status": status,
        "evidence": "classifier reason",
        "claimable": True,
    }
    if quote is not None:
        row["backing"] = {"quote": quote, "instrument": "quote"}
    return row


def _units(*pairs: tuple[str, str]) -> list[SimpleNamespace]:
    return [SimpleNamespace(path=p, text=t) for p, t in pairs]


# ── cl. 3 — the Fact ─────────────────────────────────────────────────────────


class TestQuoteInVault:
    UNITS = _units(
        ("work_experience[0].responsibilities[0]", "Connected the sample-intake LIMS to SAP via MySQL."),
        ("work_experience[0].responsibilities[1]", "通过中央接口平台，将样本接收LIMS和质量控制LIMS与SAP系统对接"),
        ("languages[0]", "Deutsch Muttersprache"),
        ("skills[3]", "团队领导"),
        ("personal_info.location", "Bremen"),
        ("work_experience[0].dates", "2019-04 – 2024-03"),
    )

    @pytest.mark.parametrize("quote,path", [
        ("Connected the sample-intake LIMS", "work_experience[0].responsibilities[0]"),
        ("  connected   THE sample-intake lims  ", "work_experience[0].responsibilities[0]"),
        ("样本接收LIMS和质量", "work_experience[0].responsibilities[1]"),  # ≥6 CJK chars
        ("Deutsch", "languages[0]"),  # a whole word of a typed value
        ("团队领导", "skills[3]"),  # a whole typed unit, any script
    ])
    def test_a_substantial_verbatim_span_in_one_unit_resolves(self, quote, path):
        assert quote_in_vault(quote, self.UNITS) == path

    @pytest.mark.parametrize("quote", [
        "LIMS",  # one word of a sentence shows nothing on its own
        "SQ",  # not token-bounded inside "MySQL"
        "LIMS和",  # too short for a CJK span
        "Connected the sample-intake LIMS to SAP via MySQL. Deutsch",  # stitched from two units
        "Bremen",  # a city backs no requirement
        "2019-04 – 2024-03",  # neither does a date line
        "Verbunden das LIMS mit SAP über MySQL",  # a translation is not a quote
        "",
        None,
    ])
    def test_anything_else_does_not(self, quote):
        assert quote_in_vault(quote, self.UNITS) is None

    @pytest.mark.parametrize("quote,unit", [
        ("bersetzung", "Übersetzung"),  # F3: a non-ASCII letter is a word character
        ("überwachung", "Prüfmittelüberwachung"),
        ("ätige Leitung der", "Tätige Leitung der Abteilung"),
    ])
    def test_a_quote_may_not_start_or_end_inside_a_non_ascii_word(self, quote, unit):
        assert quote_in_vault(quote, _units(("skills[0]", unit))) is None

    def test_an_excluded_span_blocks_only_the_matches_that_overlap_it(self):
        units = _units(("projects[0].description", "Plan: Kubernetes-Betrieb aufbauen. Betrieb der LIMS-Plattform seit 2020"))
        assert quote_in_vault("Kubernetes-Betrieb aufbauen", units, exclude=["Plan: Kubernetes-Betrieb aufbauen."]) is None
        assert quote_in_vault("Betrieb der LIMS-Plattform seit 2020", units,
                              exclude=["Plan: Kubernetes-Betrieb aufbauen."]) == "projects[0].description"


def test_a_typed_value_still_needs_a_token_boundary():
    """A typed unit lifts the length minimum, never the boundary: "SQL" is not
    the skill "MySQL"."""
    assert quote_in_vault("SQL", _units(("skills[0]", "MySQL"))) is None
    assert quote_in_vault("MySQL", _units(("skills[0]", "MySQL"))) == "skills[0]"


# ── cl. 4 — the floor's two arms ─────────────────────────────────────────────


class TestTheFloorAcceptsAVerifiedQuote:
    def test_uat3_shape_an_english_requirement_backed_by_a_german_quote_stays_claimable(self):
        """THE regression: the literal arm cannot find 'ERP integration' in a
        German vault; the classifier's verbatim quote backs it."""
        healed, violations = assert_claimable_backed([_row("ERP integration", ERP_QUOTE)], VAULT, seam="t")
        assert violations == []
        assert healed[0]["status"] == "direct" and healed[0]["claimable"] is True
        assert healed[0]["backing"]["unit_path"] == "work_experience[0].responsibilities[0]"

    def test_without_a_quote_the_same_row_still_heals_to_gap(self):
        healed, violations = assert_claimable_backed([_row("ERP integration")], VAULT, seam="t")
        assert [v["reason"] for v in violations] == ["no_vault_evidence_unit"]
        assert healed[0]["status"] == "gap" and healed[0]["claimable"] is False
        assert healed[0]["unbacked"] == UNBACKED_NO_VERIFIABLE_QUOTE

    def test_a_quote_that_is_not_in_the_vault_backs_nothing_and_is_dropped(self):
        healed, _ = assert_claimable_backed(
            [_row("ERP integration", "Integrated SAP with the LIMS")], VAULT, seam="t"
        )
        assert healed[0]["claimable"] is False
        assert "backing" not in healed[0]

    def test_an_unconfirmed_entry_backs_nothing(self):
        """ADR-061 cl. 3: the quote space is the CLAIMABLE vault."""
        healed, _ = assert_claimable_backed(
            [_row("Kubernetes operations", "Kubernetes-Betrieb im Rechenzentrum")], VAULT, seam="t"
        )
        assert healed[0]["claimable"] is False

    def test_a_denial_outranks_a_verified_quote(self):
        vault = copy.deepcopy(VAULT)
        vault["metadata"] = {"denied_concepts": [{"concept": "ERP integration", "statement": "nie",
                                                   "source": "interview", "date": "2026-11-01",
                                                   "denial_level": "direct"}]}
        healed, violations = assert_claimable_backed([_row("ERP integration", ERP_QUOTE)], vault, seam="t")
        assert violations[0]["reason"] == "denied_concept"
        assert healed[0]["status"] == "denied" and "backing" not in healed[0]

    def test_a_quote_naming_a_denied_concept_backs_no_cross_language_row(self):
        """Adversarial pass 2026-11-04 (F1): the denial matched the row's own
        concept only, so "supplier audits" stood on a quote naming the denied
        "Lieferantenaudits". It heals to gap — fail-safe, never to `denied`."""
        vault = copy.deepcopy(VAULT)
        unit = "Teilnahme an Lieferantenaudits bei drei Zulieferern in Norddeutschland"
        vault["work_experience"][0]["responsibilities"].append(unit)
        vault["metadata"] = {"denied_concepts": [{"concept": "Lieferantenaudits", "statement": "nie selbst",
                                                   "source": "interview", "date": "2026-11-01",
                                                   "denial_level": "direct"}]}
        healed, violations = assert_claimable_backed([_row("supplier audits", unit)], vault, seam="t")
        assert healed[0]["claimable"] is False and healed[0]["status"] == "gap"
        assert [v["reason"] for v in violations] == ["no_vault_evidence_unit"]

    def test_a_not_yet_reached_line_backs_nothing_wherever_its_words_repeat(self):
        """F2 (D-12): an aim repeated as a project description is still an aim."""
        aim = "Aufbau eines Kubernetes-Betriebs für die Laborplattform ist geplant und noch nicht erreicht"
        vault = copy.deepcopy(VAULT)
        vault["work_experience"][0]["not_yet_reached"] = [aim]
        vault["projects"] = [{"id": "p1", "name": "Plattform", "associated_experience": "w1",
                              "description": aim, "technologies": []}]
        healed, _ = assert_claimable_backed([_row("container orchestration", aim)], vault, seam="t")
        assert healed[0]["claimable"] is False
        # the exclusion is the aim's span only: the rest of the vault still backs
        again, violations = assert_claimable_backed([_row("ERP integration", ERP_QUOTE)], vault, seam="t")
        assert violations == [] and again[0]["claimable"] is True

    def test_an_edited_vault_entry_heals_the_row_at_the_next_floor_run(self):
        healed, _ = assert_claimable_backed([_row("ERP integration", ERP_QUOTE)], VAULT, seam="t")
        assert healed[0]["claimable"] is True
        edited = copy.deepcopy(VAULT)
        edited["work_experience"][0]["responsibilities"][0] = "Leitung von Laborprojekten."
        again, violations = assert_claimable_backed(healed, edited, seam="t")
        assert again[0]["claimable"] is False and violations

    def test_a_literal_hit_keeps_the_row_but_an_unverifiable_quote_does_not_reach_a_reviewer(self):
        healed, violations = assert_claimable_backed([_row("Python", "Python expert since 2010")], VAULT, seam="t")
        assert violations == [] and healed[0]["claimable"] is True
        assert "backing" not in healed[0]


# ── cl. 2 — the builder carries the classifier's quote ───────────────────────


def test_build_keyword_ledger_carries_evidence_quote_as_an_unverified_backing():
    led = build_keyword_ledger(
        [{"concept": "ERP integration", "status": "direct", "evidence": "r", "evidence_quote": ERP_QUOTE},
         {"concept": "Kubernetes", "status": "gap", "evidence": "", "evidence_quote": "ignored"}],
        ["ERP integration", "Kubernetes"], [], [], profile_json=VAULT,
    )
    by = {r["concept"]: r for r in led}
    assert by["ERP integration"]["backing"] == {"quote": ERP_QUOTE, "instrument": "quote"}
    assert "backing" not in by["Kubernetes"]  # a gap backs nothing


# ── cl. 6 — renderings at their true strength; cl. 5 — the second line ──────


def test_the_writer_block_never_says_not_in_the_profile_and_splits_by_provenance():
    healed, _ = assert_claimable_backed(
        [_row("ERP integration"), {**_row("TOGAF", status="gap"), "claimable": False, "evidence": ""}],
        VAULT, seam="t",
    )
    block = render_ledger_prompt_block(healed)
    assert "NOT in the profile" not in block
    assert "the analysis found no evidence" in block
    assert "no verifiable profile quote backs these" in block
    unverified_part = block.split("no verifiable profile quote backs these")[1]
    assert "ERP integration" in unverified_part and "TOGAF" not in unverified_part


def test_the_reviewer_sees_a_verified_quote_with_the_level_task_and_never_an_unverified_one():
    healed, _ = assert_claimable_backed(
        [_row("ERP integration", ERP_QUOTE), _row("Python", "Python expert since 2010")], VAULT, seam="t"
    )
    block = render_ledger_reviewer_block(healed)
    assert f'profile quote: "{ERP_QUOTE}"' in block
    assert "QUOTE CHECK (task)" in block
    assert "Python expert since 2010" not in block
    assert "NOT in the profile" not in block


def test_the_reviewer_renders_only_a_quote_the_floor_stamped():
    """Defence in depth for a legacy/carried row that never met the new floor:
    a ``backing`` without the floor's ``unit_path`` stamp is not evidence."""
    block = render_ledger_reviewer_block(
        [{**_row("ERP integration", ERP_QUOTE)}]  # backing present, no unit_path
    )
    assert ERP_QUOTE not in block and "QUOTE CHECK" not in block


def test_a_collapsed_duplicate_keeps_its_members_quote():
    """`Kubernetes` and `Kubernetes (production at scale)` collapse into one row
    keyed by the shorter concept; the quote the LONGER member carried survives."""
    led = build_keyword_ledger(
        [{"concept": "ERP integration", "status": "direct", "evidence": "a"},
         {"concept": "ERP integration (SAP S/4HANA)", "status": "direct", "evidence": "b",
          "evidence_quote": ERP_QUOTE}],
        ["ERP integration", "ERP integration (SAP S/4HANA)"], [], [], profile_json=VAULT,
    )
    rows = [r for r in led if r["concept"].startswith("ERP")]
    assert len(rows) == 1 and rows[0]["backing"]["quote"] == ERP_QUOTE


def test_no_quote_check_task_when_nothing_is_quoted():
    healed, _ = assert_claimable_backed([_row("Python")], VAULT, seam="t")
    assert "QUOTE CHECK" not in render_ledger_reviewer_block(healed)


# ── one named seam test per floor call site (ADR-100 cl. 4; SF-GAP.29) ───────
# Each drives the transformation that call site composes before the floor and
# asserts the verified quote SURVIVES it: a seam that drops `backing` restores
# the UAT-3 heal silently.


def _backed_ledger() -> list[dict]:
    healed, _ = assert_claimable_backed(
        [_row("ERP integration", ERP_QUOTE), {**_row("TOGAF", status="gap"), "claimable": False, "evidence": ""}],
        VAULT, seam="t",
    )
    return healed


def _assert_still_backed(ledger: list[dict]) -> None:
    row = next(r for r in ledger if r["concept"] == "ERP integration")
    assert row["claimable"] is True and row["status"] == "direct"
    assert row["backing"]["unit_path"] == "work_experience[0].responsibilities[0]"


def test_seam_gap_analysis_downgrade_keeps_an_untouched_backed_row():  # gap.py downgrade_keyword_liability
    led, _ = downgrade_ledger_for_concepts(_backed_ledger(), ["TOGAF"])
    _assert_still_backed(assert_claimable_backed(led, VAULT, seam="gap-analysis downgrade")[0])


def test_seam_gap_analysis_merge_keeps_a_carried_backed_row():  # gap.py _apply_floors_to_merged
    from applire.services.gap import _apply_floors_to_merged, merge_ledger_per_requirement

    previous = _backed_ledger()
    fresh = [{**_row("ERP integration"), "status": "partial"}, previous[1]]  # an un-touched downward move
    merged, carried = merge_ledger_per_requirement(
        fresh, previous, jd_terms=["ERP integration", "TOGAF"], touched_members=[]
    )
    _assert_still_backed(_apply_floors_to_merged(merged, carried, denied_concepts=[], profile_json=VAULT))


def test_seam_interview_vault_reevaluation_keeps_a_backed_row():  # session.py interview vault re-evaluation
    led, _ = reevaluate_gap_ledger_against_vault(_backed_ledger(), VAULT)
    _assert_still_backed(assert_claimable_backed(led, VAULT, seam="interview vault re-evaluation")[0])


@pytest.mark.parametrize("seam", ["interview #188 upgrade", "agent submit_claims"])
def test_seam_ledger_upgrade_keeps_a_backed_row(seam):  # session.py + agent_bridge.py
    led, _ = upgrade_ledger_for_concepts(_backed_ledger(), ["TOGAF"], "Ich habe TOGAF angewendet.")
    _assert_still_backed(assert_claimable_backed(led, VAULT, seam=seam)[0])


@pytest.mark.asyncio
async def test_seam_document_read_refresh_persists_a_backed_row():  # keyword_ledger.refresh_persist_and_rescore
    class _Db:
        async def flush(self):
            return None

    gap = SimpleNamespace(keyword_ledger=_backed_ledger(), match_score=0.0, requirement_breakdown=None)
    out = await refresh_persist_and_rescore(gap, VAULT, _Db(), seam="cv generation")
    _assert_still_backed(out)


@pytest.mark.asyncio
async def test_seam_gap_analysis_build_reasks_and_persists_a_backed_row(monkeypatch):  # gap.py _run_analysis
    """The UAT-3 path end to end through ``_reask_unverified_quotes``: the first
    answer cites a translation (not verbatim), the targeted re-ask returns the
    verbatim German span, and the persisted row is claimable with that quote."""
    from applire.services import gap as gap_mod

    calls: list[str] = []

    async def fake_parse(provider, site, prompt, **kwargs):
        calls.append(prompt)
        if "RE-ASK" in prompt:
            return {"classifications": [
                {"requirement": "ERP integration", "status": "direct", "reason": "r", "evidence_quote": ERP_QUOTE}]}
        return {"classifications": [
            {"requirement": "ERP integration", "status": "direct", "reason": "r",
             "evidence_quote": "Connected the LIMS to SAP S/4HANA"},
            {"requirement": "Python", "status": "direct", "reason": "r", "evidence_quote": "Python"}]}

    monkeypatch.setattr(gap_mod._prompt_context, "aparse_json", fake_parse)
    data = await gap_mod._reask_unverified_quotes(object(), "USER", await fake_parse(None, "", "USER"), VAULT)
    assert len(calls) == 2 and "'ERP integration'" in calls[1] and "'Python'" not in calls[1]
    rows = [gap_mod.ledger_input_from_classification(c) for c in data["classifications"]]
    led = build_keyword_ledger(rows, ["ERP integration", "Python"], [], [], profile_json=VAULT)
    _assert_still_backed(assert_claimable_backed(led, VAULT, seam="gap-analysis build")[0])


@pytest.mark.asyncio
async def test_a_failed_reask_keeps_the_first_answer(monkeypatch):
    from applire.services import gap as gap_mod

    async def boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(gap_mod._prompt_context, "aparse_json", boom)
    first = {"classifications": [{"requirement": "ERP integration", "status": "direct", "evidence_quote": "x y z"}]}
    assert await gap_mod._reask_unverified_quotes(object(), "USER", first, VAULT) is first


@pytest.mark.asyncio
async def test_no_reask_for_a_row_the_literal_arm_already_backs(monkeypatch):
    """The re-ask costs a provider call; a row whose own term grounds literally
    (the floor keeps it without a quote) never triggers one."""
    from applire.services import gap as gap_mod

    calls: list[str] = []

    async def count(provider, site, prompt, **kwargs):  # the re-ask swallows errors: count, never raise
        calls.append(prompt)
        return {"classifications": []}

    monkeypatch.setattr(gap_mod._prompt_context, "aparse_json", count)
    first = {"classifications": [{"requirement": "Python", "status": "direct", "reason": "r"}]}
    await gap_mod._reask_unverified_quotes(object(), "USER", first, VAULT)
    assert calls == []
