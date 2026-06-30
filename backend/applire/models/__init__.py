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

# Import all models so SQLAlchemy can resolve FK string references (e.g. "companies.id")
# at mapper-configuration time, regardless of which service is imported first.
from applire.models import (  # noqa: F401
    application,
    color_profile,
    color_scheme,
    company,
    cv,
    flow,
    gap,
    import_job,
    job,
    profile,
    session,
    uploads,
    user,
    user_settings,
)
