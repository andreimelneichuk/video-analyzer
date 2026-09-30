import logging

import redis
from rq import Queue

from src.config import settings
from src.workers.memory_queue import InProcessQueue

logger = logging.getLogger(__name__)

QUEUE_NAME = "skycoach_reels"

_redis_conn: redis.Redis | None = None
_task_queue: Queue | None = None
_memory_queue: "InProcessQueue | None" = None


def get_redis_connection() -> redis.Redis:
    """Returns shared Redis connection."""
    global _redis_conn
    if _redis_conn is None:
        _redis_conn = redis.from_url(settings.REDIS_URL)
    return _redis_conn


def get_task_queue() -> Queue:
    """Returns RQ task queue instance."""
    global _task_queue
    if _task_queue is None:
        conn = get_redis_connection()
        _task_queue = Queue(
            name=QUEUE_NAME,
            connection=conn,
            default_timeout=settings.TASK_TIMEOUT_SECONDS,
        )
    return _task_queue


def uses_memory_queue() -> bool:
    return settings.QUEUE_BACKEND == "memory"


def get_memory_queue() -> InProcessQueue:
    """Returns the in-process queue; created and started by the app lifespan."""
    global _memory_queue
    if _memory_queue is None:
        from src.workers.tasks import process_reel_task

        _memory_queue = InProcessQueue(process_reel_task)
    return _memory_queue


def enqueue_reel_analysis(task_id: str) -> None:
    """
    Submits a task ID to the background Redis queue.
    If Redis is unavailable (e.g. in offline unit tests without Redis daemon),
    logs a warning.
    """
    if uses_memory_queue():
        get_memory_queue().enqueue(task_id)
        return
    try:
        q = get_task_queue()
        q.enqueue(
            "src.workers.tasks.process_reel_task",
            task_id=task_id,
            job_timeout=settings.TASK_TIMEOUT_SECONDS,
            result_ttl=86400,
        )
        logger.info("Enqueued task %s to queue '%s'", task_id, QUEUE_NAME)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Could not connect to Redis at %s to enqueue task %s: %s",
            settings.REDIS_URL,
            task_id,
            e,
        )
