# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""WP-C (GARH, Sorbet invite gate) — the open points of a reviewed final draft reach the
person as ONE class-A decision (end state C-1), bound to the hash of those points.

Before: ``terminal-review-open`` was class S (ADR-099 cl. 8 "open notes S") — the loop's
unresolved findings were in the report only, never in front of the person.
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.finding_registry import annotate_report  # noqa: E402
from applire.services.terminal_review_outcome import (  # noqa: E402
    CorrectionFacts,
    TerminalNote,
    TerminalReviewOutcome,
    build_terminal_review_check,
)


def _open(blocking=("The summary overstates the title.",), notes=()):
    return TerminalReviewOutcome(
        chain_id="cv_terminal_review", path="exhausted", approved=False,
        blocking_issues=tuple(blocking), minor_issues=(), rounds=3, notes=tuple(notes),
        correction=CorrectionFacts(delivered_is_reviewed=True),
    )


def test_open_points_hash_is_set_only_on_the_open_state_and_tracks_the_points():
    a = build_terminal_review_check(_open())
    assert a.details_key == "terminal-review-open" and a.status == "fail"
    assert a.open_points_hash and len(a.open_points_hash) == 16
    b = build_terminal_review_check(_open(blocking=("Another point.",)))
    assert b.open_points_hash != a.open_points_hash
    c = build_terminal_review_check(_open(notes=(TerminalNote(kind="code", key="cv.skills_cap", text="over"),)))
    assert c.open_points_hash != a.open_points_hash
    clean = TerminalReviewOutcome(chain_id="x", path="approved", approved=True,
                                  blocking_issues=(), minor_issues=(), rounds=1)
    assert build_terminal_review_check(clean).open_points_hash is None


def test_open_terminal_review_is_class_a_decide_open_not_silent():
    check = build_terminal_review_check(_open()).model_dump(mode="json")
    report = {"checks": [check], "delivery": {"loop_clean": True}}
    unregistered = annotate_report(report)
    assert not unregistered
    assert (check["delivery_class"], check["resolver"]) == ("A", "decide_open")


def test_after_lets_the_reviewed_final_verdict_supersede_an_earlier_invocations_findings():
    """M6 kill: the re-entry fold. The earlier invocation's finding read a draft that no
    longer ships; the later verdict read the delivered one, so it is the statement —
    even when it names fewer reviewer findings (a code fact kept the loop open).
    ``worse_of`` would resurrect the stale finding: it ranks (fail, has-finding) higher."""
    earlier = _open(blocking=("Weberit carries three bullets.",))
    # the final verdict raised only a minor note; a deterministic check had reopened
    # the round (the WP-FR F3 shape) — the verdict over the delivered draft is clean
    later = TerminalReviewOutcome(
        chain_id="cv_terminal_review", path="exhausted", approved=False,
        blocking_issues=(), minor_issues=("A wording note.",), rounds=1,
        correction=CorrectionFacts(delivered_is_reviewed=True),
    )
    folded = later.after(earlier)
    assert folded.blocking_issues == () and folded.status == "pass" and folded.rounds == 4
    assert later.worse_of(earlier).blocking_issues == ("Weberit carries three bullets.",)


def test_after_falls_back_to_worse_of_when_the_later_verdict_did_not_read_the_delivered_draft():
    earlier = _open(blocking=("Weberit carries three bullets.",))
    later = TerminalReviewOutcome(
        chain_id="cv_terminal_review", path="exhausted", approved=False,
        blocking_issues=(), minor_issues=("A wording note.",), rounds=1,
        correction=CorrectionFacts(delivered_is_reviewed=False),
    )
    assert later.after(earlier).blocking_issues == ("Weberit carries three bullets.",)
    assert later.after(None) is later
