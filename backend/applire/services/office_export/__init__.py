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

"""Office export (ADR-079, E057) — the editable working copy.

A ``.docx`` written **directly** with ``python-docx`` from the same structured
data the PDF renders from. There is deliberately no HTML, no template engine and
no subprocess on this path (ADR-079 clause 2): converting the fourteen
presentation templates was measured to destroy two of seven CV layouts, and the
LibreOffice variant scored identically while costing 361 MB in the image.

The export is **not** the norms-checked PDF and never claims to be. Its ADR-039
audit runs over text extracted back out of the produced file, and the
page-length band is reported ``not_applicable`` with its reason rather than
guessed (ADR-079 clause 4) — a ``.docx`` has no pages until a word processor
lays it out.
"""
