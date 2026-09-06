"""One durable due check per project; atomic runs coordinate server workers."""

import asyncio
import logging
import time
from datetime import timedelta

from visp_memory.core.clock import utc_now

logger = logging.getLogger(__name__)
IDLE_SECONDS = 300
POLL_SECONDS = 60


def run_due(dreaming):
    now = utc_now()
    with dreaming.storage._get_db() as conn:
        repo_ids = [
            row["repo_id"]
            for row in conn.execute(
                """
            SELECT d.repo_id FROM dream_projects d JOIN repositories r ON r.id = d.repo_id
            WHERE d.enabled = 1 AND d.next_run <= ? AND r.status = 'active'
            ORDER BY d.next_run LIMIT 10""",
                (now.isoformat(),),
            )
        ]
    for repo_id in repo_ids:
        try:
            dreaming.run(repo_id, actor_id="dreaming-scheduler", scheduled=True)
        except Exception as error:
            logger.warning("Dreaming cycle failed (%s)", type(error).__name__)
            # Failed transactions have rolled back; make failure visible and retry later.
            with dreaming.storage._get_db() as conn:
                conn.execute(
                    "UPDATE dream_projects SET last_error = ?, next_run = ? WHERE repo_id = ?",
                    (
                        "The last cycle failed; no partial cleanup was committed.",
                        (utc_now() + timedelta(minutes=10)).isoformat(),
                        repo_id,
                    ),
                )
                conn.commit()


async def dreaming_loop(app, stop):
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_SECONDS)
        except asyncio.TimeoutError:
            if time.monotonic() - app.state.dream_last_activity < IDLE_SECONDS:
                continue
            try:
                await asyncio.to_thread(run_due, app.state.dreaming)
            except Exception:
                logger.exception("Unable to check dreaming schedule")
