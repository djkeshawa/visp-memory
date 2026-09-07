"""One durable due check per project; atomic runs coordinate server workers."""

import asyncio
import logging
import time

from visp_memory.core.clock import utc_now

logger = logging.getLogger(__name__)
IDLE_SECONDS = 300
POLL_SECONDS = 60


def run_due(dreaming):
    now = utc_now()
    repo_ids = dreaming.due_projects(now)
    for repo_id in repo_ids:
        try:
            dreaming.run(repo_id, actor_id="dreaming-scheduler", scheduled=True)
        except Exception as error:
            logger.warning("Dreaming cycle failed (%s)", type(error).__name__)
            # Failed transactions have rolled back; make failure visible and retry later.
            dreaming.record_failure(repo_id)


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
