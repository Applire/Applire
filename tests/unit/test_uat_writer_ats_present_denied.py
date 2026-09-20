# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""F-8 (#672 line 125) — a PRESENT ATS keyword the candidate DENIED gets its own bucket.

The founder UAT of 2026-09-20 reported `Agile methodologies` and `DevOps` under
`keywords.present` while the gap engine recorded both as `denied` in the same run: a
denied capability counted as an ATS win.

Why a new bucket and not `present_unsupported`: `ats_audit.py`'s own ADR-048/059 note
(amended 2026-07-27) scopes that quadrant to UNKNOWN gaps — "a denied concept named in
an honest negation is not an unsupported claim… the direction-aware check lives with the
Oracle". Judging whether the sentence around the keyword is an honest negation or a
claim is exactly what this layer may not do. Reporting *that the owning ledger row is
`denied`* is a FACT (ADR-062), and `present`/`missing` and both coverage counters stay
byte-identical — the keyword IS literally on the page.

Ownership of a keyword follows `keyword_present`'s own rule (`_entry_norms`, ADR-048
§8/#122), so the presence predicate and this bucket can never disagree about which row
a keyword belongs to.
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.ats_audit import _keyword_coverage, _norm  # noqa: E402

_DOC = _norm(
    "I have used agile ways of working and DevOps tooling, and I work in Python daily. "
    "I have not personally owned the platform."
)


def _ledger():
    return [
        {
            "concept": "Agile methodologies",
            "surface_forms": ["Agile Collaboration", "agile"],
            "status": "denied",
            "claimable": False,
            "evidence": "Candidate explicitly stated a limit here (interview).",
        },
        {"concept": "DevOps", "surface_forms": ["DevOps"], "status": "denied", "claimable": False},
        {"concept": "Python", "surface_forms": ["Python"], "status": "direct", "claimable": True},
        {"concept": "Kubernetes", "surface_forms": ["Kubernetes"], "status": "gap", "claimable": False},
    ]


def test_a_present_keyword_whose_ledger_row_is_denied_lands_in_its_own_bucket():
    cov = _keyword_coverage(
        _DOC, ["Agile methodologies", "DevOps", "Python", "Kubernetes"], _ledger()
    )
    assert cov.present_denied == ["Agile methodologies", "DevOps"]


def test_present_and_both_counters_are_unchanged_by_the_new_bucket():
    """The back-compat contract the MCP payload, the coverage tile and the
    `present`/`missing` partition all rest on."""
    keywords = ["Agile methodologies", "DevOps", "Python", "Kubernetes"]
    cov = _keyword_coverage(_DOC, keywords, _ledger())
    assert cov.present == ["Agile methodologies", "DevOps", "Python"]
    assert cov.missing == ["Kubernetes"]
    # Every keyword is in exactly one of present/missing — the partition survives.
    assert sorted(cov.present + cov.missing) == sorted(keywords)
    assert set(cov.present_denied) <= set(cov.present)


def test_a_denied_keyword_is_not_folded_into_present_unsupported():
    """ADR-048/059 amended 2026-07-27: that quadrant is the UNKNOWN-gap population, and
    this layer is deliberately negation-blind. Folding a denial in would be the
    judgement the ADR assigns to the Oracle."""
    cov = _keyword_coverage(_DOC, ["Agile methodologies", "DevOps"], _ledger())
    assert cov.present_unsupported == []
    assert cov.present_denied


def test_without_a_ledger_nothing_is_claimed_either_way():
    cov = _keyword_coverage(_DOC, ["DevOps"], None)
    assert cov.present == ["DevOps"]
    assert cov.present_denied == []


def test_an_absent_denied_keyword_is_not_reported_as_present_denied():
    """The asserted baseline: the bucket is scoped to keywords the document CARRIES."""
    cov = _keyword_coverage(_norm("Nothing relevant here."), ["DevOps"], _ledger())
    assert cov.present == [] and cov.present_denied == []


def test_the_bucket_reaches_the_cv_report_and_is_counted_nowhere_else():
    from applire.services.ats_audit import _audit_cv_text

    from applire.schemas.cv import TailoredCVData

    tailored = TailoredCVData.model_validate(
        {
            "contact": {"name": "Anna Bauer", "email": None, "phone": None, "location": None},
            "summary": "I have used agile ways of working and DevOps tooling.",
            "work_history": [
                {"id": "w1", "company": "Acme", "role": "Lead", "start_date": "2020-01",
                 "end_date": None, "bullets": ["ran the shift plan"], "projects": []}
            ],
            "skills": [],
            "education": [],
            "languages": [],
        }
    )
    report = _audit_cv_text(
        "Anna Bauer agile DevOps", tailored, keywords=["DevOps"], ledger=_ledger()
    )
    assert report.keywords.present_denied == ["DevOps"]
    assert "DevOps" in report.keywords.present
    # No check status changed: this is a keyword FACT, not a new check.
    assert report.failed == sum(1 for c in report.checks if c.status == "fail")


def test_the_agent_door_field_list_carries_the_new_key():
    """`mcp/server.py`'s ADR-084 field lists are the agent-door contract — a new report
    field the agent cannot see is a field the agent channel does not have."""
    from applire.mcp.server import _JD_DERIVED_FIELDS

    assert "keywords.present_denied" in _JD_DERIVED_FIELDS["ats_report"]
    assert "ats_report.keywords.present_denied" in _JD_DERIVED_FIELDS["render_document"]
