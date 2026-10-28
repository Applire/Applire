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
"""ADR-099 — the ONE finding registry: every kind a user can be shown after a
generation or import, mapped to its delivery class and the one action that
resolves it.

Classes (ADR-099 cl. 2):
  F — fixed by the generation loop before delivery; not rendered while the
      loop ran clean (cl. 4(d)); its fallback class applies otherwise.
  A — needs the user: ONE action (one control, or one question with a closed
      answer set) whose every answer clears the finding on recompute.
  U — the producer did not run: rendered as unknown with ONE re-run action.
  S — silent: report and operator log only.

CONTRACT COMMIT (main session, UAT-2 fix round): the vocabulary below is
fixed — WP-B fills ``REGISTRY`` and wires ``delivery_class``/``resolver`` into
the report; WP-C and WP-A2 render from those fields. Add keys; never rename.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

DeliveryClass = Literal["F", "A", "U", "S"]

#: Resolver vocabulary — the one action a class-A/U row offers (ADR-099 cl. 6–11).
RESOLVERS: frozenset[str] = frozenset(
    {
        "ask_station",        # misattributed / restatementBorrowed: "Wohin gehört das?" (owning stations + Entfernen)
        "ask_true",           # unbacked kinds: "Stimmt das?" (Ja + wo → testimony; no change → Entfernen; Nein → take-out)
        "ask_achieved",       # inflatedAspirational: "Ist das erreicht?" (Ja → testimony; Nein → rewritten as goal)
        "remove_only",        # residue whose only end state is take-out
        "page_length",        # ONE card: keep N pages / cut to target (closest reachable)
        "translate",          # mixed-language residue: "Diese Stellen übersetzen"
        "recheck",            # class U: "Jetzt prüfen" — full report re-run on this version
        "regenerate",         # class U terminal-review-not-run: "Neue Fassung erstellen"
        "answer_unasked",     # letter unasked requirements: "Jetzt beantworten"
        "signature",          # letter only: add signature / "Ohne Unterschrift"
        "open_gap_analysis",  # group 3: one link to the gap analysis
        "conflict_list",      # hub: four outcomes (keep · take · keep both · merge)
        "conflict_scalar",    # hub: two outcomes
        "confirmation",       # hub: parked question + its options (unchanged)
        "recompute_span",     # hub span_provenance (unchanged)
        "confirm_match",      # hub suspect_binding / recognised pair: Ja / Nein-getrennt
        "unit_input",         # hub unit: the input in place
        "adopt_not_applied",  # hub not_applied: "Übernehmen" per item
        "complete_section",   # hub completeness with field_gaps: "Verbessern"
    }
)


@dataclass(frozen=True)
class Entry:
    cls: DeliveryClass
    #: class-A/U action; for class F the resolver of the A/U FALLBACK (cl. 4(d)/(e)).
    resolver: Optional[str]
    #: class F only — the class its residue / no-loop render falls back to.
    fallback: Optional[DeliveryClass] = None


#: kind key → Entry. Kind keys: "ats:<check id>", "oracle:<detail key>",
#: "keyword:<bucket>", "critic:<kind>", "health:<thread>", "page:<signal>".
#: Filled by WP-B (document side) and WP-A1 (health side).
REGISTRY: dict[str, Entry] = {}


def classify(kind: str) -> Entry:
    """The registry entry for ``kind``. Raises ``KeyError`` for an unregistered
    kind — the CI test (T-registry) enumerates every producer's kinds through
    this, so a new kind without an entry fails the build instead of rendering
    raw or vanishing."""
    return REGISTRY[kind]


# UAT-3d (Hazelnut build 5, WP-A1 hub) — the profile-health hub's kinds.
REGISTRY.update(
    {
        "health:conflict_list": Entry("A", "conflict_list"),
        "health:conflict_scalar": Entry("A", "conflict_scalar"),
        "health:confirmation": Entry("A", "confirmation"),
        "health:unit": Entry("A", "unit_input"),
        "health:span_provenance": Entry("A", "recompute_span"),
        "health:suspect_binding": Entry("A", "confirm_match"),
        "health:duplicate_entry": Entry("A", "confirm_match"),
        "health:not_applied": Entry("A", "adopt_not_applied"),
        "health:not_applied_unadoptable": Entry("S", None),
        "health:accuracy": Entry("S", None),
        "health:completeness": Entry("A", "complete_section"),
        "health:completeness_no_field_gaps": Entry("S", None),
    }
)
