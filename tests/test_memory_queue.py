import asyncio
import threading
import time

from src.models import Task, TaskStatus
from src.workers import queue as queue_module
from src.workers.memory_queue import InProcessQueue, requeue_unfinished


async def test_runs_tasks_one_at_a_time_in_order():
    processed: list[str] = []
    running = 0
    max_running = 0

    def runner(task_id: str) -> None:
        nonlocal running, max_running
        running += 1
        max_running = max(max_running, running)
        time.sleep(0.02)
        processed.append(task_id)
        running -= 1

    q = InProcessQueue(runner)
    q.start()
    for task_id in ("a", "b", "c"):
        q.enqueue(task_id)
    await asyncio.wait_for(q.join(), timeout=5)
    await q.stop()

    assert processed == ["a", "b", "c"]
    assert max_running == 1


async def test_blocking_pipeline_does_not_freeze_event_loop():
    """yt-dlp/ffmpeg block; the web app must keep answering status polls meanwhile."""
    release = threading.Event()
    q = InProcessQueue(lambda task_id: release.wait(timeout=5))
    q.start()
    q.enqueue("slow")

    ticks = 0
    for _ in range(5):
        await asyncio.sleep(0.01)
        ticks += 1

    release.set()
    await asyncio.wait_for(q.join(), timeout=5)
    await q.stop()
    assert ticks == 5


async def test_failing_task_does_not_stop_the_queue():
    processed: list[str] = []

    def runner(task_id: str) -> None:
        if task_id == "boom":
            raise RuntimeError("pipeline crashed")
        processed.append(task_id)

    q = InProcessQueue(runner)
    q.start()
    q.enqueue("boom")
    q.enqueue("ok")
    await asyncio.wait_for(q.join(), timeout=5)
    await q.stop()

    assert processed == ["ok"]


async def test_requeue_unfinished_tasks_after_restart(db_session):
    """Tasks cut off by a restart go back to PENDING and into the queue."""
    statuses = {
        "pending": TaskStatus.PENDING,
        "downloading": TaskStatus.DOWNLOADING,
        "analyzing": TaskStatus.ANALYZING,
        "done": TaskStatus.COMPLETED,
        "failed": TaskStatus.FAILED,
    }
    for task_id, status in statuses.items():
        db_session.add(
            Task(id=task_id, original_url=task_id, canonical_url=task_id, status=status.value)
        )
    await db_session.commit()

    enqueued: list[str] = []
    requeued = await requeue_unfinished(db_session, enqueued.append)

    assert sorted(requeued) == ["analyzing", "downloading", "pending"]
    assert sorted(enqueued) == sorted(requeued)
    for task_id in requeued:
        task = await db_session.get(Task, task_id)
        assert task.status == TaskStatus.PENDING.value


def test_enqueue_uses_memory_backend(monkeypatch):
    enqueued: list[str] = []

    class FakeQueue:
        def enqueue(self, task_id: str) -> None:
            enqueued.append(task_id)

    monkeypatch.setattr("src.config.settings.QUEUE_BACKEND", "memory")
    monkeypatch.setattr(queue_module, "get_memory_queue", lambda: FakeQueue())
    monkeypatch.setattr(
        queue_module, "get_task_queue", lambda: (_ for _ in ()).throw(AssertionError("no Redis"))
    )

    queue_module.enqueue_reel_analysis("task-1")

    assert enqueued == ["task-1"]


def test_instagram_pacing_works_without_redis(monkeypatch):
    """Memory mode has no Redis: the pause between Instagram requests must still hold."""
    from src.services.extractor import ig_session

    monkeypatch.setattr("src.config.settings.QUEUE_BACKEND", "memory")
    monkeypatch.setattr("src.config.settings.IG_MIN_INTERVAL_SEC", 30)
    monkeypatch.setattr("src.config.settings.IG_JITTER_SEC", 0)
    monkeypatch.setattr(ig_session, "_memory_next_slot", 0.0)
    monkeypatch.setattr(
        "src.workers.queue.get_redis_connection",
        lambda: (_ for _ in ()).throw(AssertionError("no Redis")),
    )
    sleeps: list[float] = []
    monkeypatch.setattr(ig_session.time, "sleep", sleeps.append)

    ig_session.wait_for_instagram_slot()
    ig_session.wait_for_instagram_slot()

    assert len(sleeps) == 1
    assert 29 < sleeps[0] <= 30
