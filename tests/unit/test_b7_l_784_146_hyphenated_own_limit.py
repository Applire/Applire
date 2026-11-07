# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 7, WP-L — 784:146: the honest gap sentence of a NAMED (recorded) gap
is the candidate's own limit on the letter's keyword audit, also for a hyphenated term.

Blind Kaile, UX audit round 3: "Mit Make-or-buy-Entscheidungen habe ich noch nicht
gearbeitet; … werde ich mich schnell einarbeiten" drew `present_unsupported` →
`stop_and_fix`. Under RULING L-1 / L-2 = B the letter names only recorded denials, so the
term is `denied` and the sentence lands in `present_denied`; the own-limit test that
moves it to `present_unsupported_in_denial` compared the RAW form against `_norm`-ed
sentence text and never matched a hyphenated term (replayed on the round-3 letter).
Synthetic data; both own-limit arms (denied row; unknown row with recorded denials)."""
from __future__ import annotations

import sys
import types
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.ats_audit import (  # noqa: E402
    _audit_letter_text,
    grounding_vault_index,
    non_claim_names_for_job,
)

_JOB = types.SimpleNamespace(role_title="Senior Controller", company_name="Kessler Antriebe GmbH")
_TERM = "Make-or-buy-Entscheidungen"
_SENT = ("Mit Make-or-buy-Entscheidungen habe ich noch nicht gearbeitet; meine Erfahrung mit "
         "Investitionsrechnungen ist dafür anschlussfähig, und in dieses Themenfeld werde ich "
         "mich schnell einarbeiten.")


def _row(status):
    return {"status": status, "concept": _TERM, "surface_forms": [_TERM], "claimable": False,
            "fit_weight": 1.0, "sources": ["required"],
            **({"denial_level": "direct"} if status == "denied" else {})}


def _audit(sentence, status, denied=()):
    paras = ["Sehr geehrte Damen und Herren,", "Bei der Rheinfeld AG verantworte ich das Werkscontrolling. " + sentence]
    profile = {
        "personal_info": {"name": "Lena Hartwig"},
        "work_experience": [{"id": "w1", "company": "Rheinfeld AG", "role": "Werkscontrollerin",
                             "start_date": "2021-03", "responsibilities": ["Werkscontrolling."]}],
        "skills": [],
        "metadata": {"denied_concepts": [
            {"date": "2026-11-07", "source": "interview", "denial_level": "direct", **d} for d in denied
        ]},
    }
    rep = _audit_letter_text(
        "\n\n".join(paras), {"header": {}, "recipient": {}, "body": {"paragraphs": paras}}, [_TERM],
        [_row(status)], vault_index=grounding_vault_index(profile),
        non_claim=non_claim_names_for_job(_JOB, None), document_language="de")
    k = rep.keywords
    return k.present_unsupported, k.present_denied, k.present_unsupported_in_denial


_DENIAL = {"concept": _TERM, "statement": "Make-or-buy-Entscheidungen habe ich nie verantwortet."}


def test_a_named_denials_hyphenated_gap_sentence_is_the_candidates_own_limit():
    unsupported, denied, in_denial = _audit(_SENT, "denied", [_DENIAL])
    assert unsupported == [] and denied == [] and in_denial == [_TERM]


def test_a_claim_of_the_hyphenated_denied_term_keeps_its_card():
    unsupported, denied, in_denial = _audit(
        "Make-or-buy-Entscheidungen habe ich für drei Werke vorbereitet.", "denied", [_DENIAL])
    assert denied == [_TERM] and in_denial == []


def test_the_d4b_arm_reads_a_hyphenated_unknown_term_in_its_negated_sentence():
    # A candidate WITH recorded limits (another one) states this limit in the letter.
    other = {"concept": "SAP S/4HANA", "statement": "S/4HANA habe ich nicht eingeführt."}
    unsupported, denied, in_denial = _audit(_SENT, "gap", [other])
    assert unsupported == [] and in_denial == [_TERM]
