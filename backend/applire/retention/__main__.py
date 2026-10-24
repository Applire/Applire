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

"""``python -m applire.retention`` — its own process, so its own declared owner
context: ``unscoped("retention")`` (ADR-092 cl. 7/8). ``run()`` sets it too
(around ``_sweep``); setting it here as well covers the run-record write."""

import asyncio
import sys

from applire import ownership
from applire.retention.worker import run, run_stale_jobs


async def _main() -> None:
    with ownership.unscoped("retention"):
        # 789:14: `--stale-jobs` runs only the two stale-generation reapers
        # (between the daily sweeps); no argument = the full daily sweep.
        if "--stale-jobs" in sys.argv[1:]:
            await run_stale_jobs()
        else:
            await run()


asyncio.run(_main())
