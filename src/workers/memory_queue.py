"""
In-process task queue for single-container deployments without Redis
(e.g. Render free tier). Tasks run one at a time in a worker thread, so the
blocking yt-dlp/ffmpeg pipeline never freezes the web app's event loop.
Queued tasks are lost on restart; requeue_unfinished() picks them up again
from the database on startup.
"""

import asyncio
import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Task, TaskStatus

logger = logging.getLogger(__name__)

UNFINISHED_STATUSES = (
    TaskStatus.PENDING.value,
    TaskStatus.DOWNLOADING.value,
    TaskStatus.ANALYZING.value,
)


class InProcessQueue:
    def __init__(self, runner: Callable[[str], None]):
        self._runner = runner
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._consumer: asyncio.Task | None = None

    def start(self) -> None:
        self._consumer = asyncio.create_task(self._consume())

    async def stop(self) -> None:
        if self._consumer is not None:
            self._consumer.cancel()
            try:
                await self._consumer
            except asyncio.CancelledError:
                pass
            self._consumer = None

    def enqueue(self, task_id: str) -> None:
        self._queue.put_nowait(task_id)
        logger.info("Enqueued task %s to in-process queue", task_id)

    async def join(self) -> None:
        await self._queue.join()

    async def _consume(self) -> None:
        while True:
            task_id = await self._queue.get()
            try:
                await asyncio.to_thread(self._runner, task_id)
            except Exception:
                logger.exception("Task %s crashed in in-process queue", task_id)
            finally:
                self._queue.task_done()


async def requeue_unfinished(session: AsyncSession, enqueue: Callable[[str], None]) -> list[str]:
    """Resets tasks interrupted by a restart to PENDING and enqueues them again."""
    result = await session.execute(select(Task).where(Task.status.in_(UNFINISHED_STATUSES)))
    tasks = list(result.scalars())
    for task in tasks:
        task.status = TaskStatus.PENDING.value
    await session.commit()
    for task in tasks:
        enqueue(task.id)
    if tasks:
        logger.info("Requeued %d unfinished tasks after restart", len(tasks))
    return [task.id for task in tasks]
