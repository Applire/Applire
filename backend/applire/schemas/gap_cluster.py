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

from typing import Literal

from pydantic import BaseModel


class GapClusterSchema(BaseModel):
    """One gap cluster as it is persisted on ``gap_analyses.gap_clusters``.

    ``category`` is **derived, not solicited** (#675 line 60, prompt
    ``gap_clustering`` v2): it is "C" when any member gap came from the
    analysis's Category C list, else "B" — a fact about which input list the
    member came from, which the caller already holds (ADR-062 clause 1). The
    clustering prompt used to state that rule and the model broke it; the
    default here exists so a model response that omits the field validates,
    and ``services/gap.py::_reconcile_cluster_categories`` then sets it.
    Readers (``interview_graph.plan_clusters``, the gaps screen, the agent
    door) see the same two letters as before.
    """

    id: str
    label: str
    category: Literal["B", "C"] = "B"
    gaps: list[str]
    jd_skills: list[str]
    jd_context: str
