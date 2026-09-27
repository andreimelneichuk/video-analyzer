import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.models import Task, TaskStatus
from src.schemas import TaskBatchCreateRequest, TaskBatchResponse, TaskItemResponse
from src.services.cache_service import TaskCacheService
from src.services.extractor.normalizer import normalize_video_url
from src.workers.queue import enqueue_reel_analysis

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", response_model=TaskBatchResponse, status_code=status.HTTP_200_OK)
async def create_tasks_batch(
    payload: TaskBatchCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Accepts a batch of up to 20 Instagram Reels / Shorts URLs.
    Checks DB cache to return immediately cached results, enqueuing new ones.
    """
    raw_urls = [u.strip() for u in payload.urls if u.strip()]
    if not raw_urls:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Список ссылок не может быть пустым.",
        )

    if len(raw_urls) > 20:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Максимальное количество ссылок за один запрос — 20.",
        )

    response_items: list[TaskItemResponse] = []
    created_count = 0
    cached_count = 0

    for raw_url in raw_urls:
        canonical_url = normalize_video_url(raw_url)

        # 1. Invalid URL check
        if not canonical_url:
            failed_task = Task(
                id=str(uuid.uuid4()),
                original_url=raw_url,
                canonical_url=raw_url,
                status=TaskStatus.FAILED.value,
                error_message="Некорректная ссылка на ролик (поддерживаются Instagram Reels, YouTube Shorts, TikTok)",
            )
            db.add(failed_task)
            await db.commit()
            await db.refresh(failed_task)
            item = TaskItemResponse.model_validate(failed_task)
            item.is_cached = False
            response_items.append(item)
            continue

        # 2. Cache check for completed task
        cached_task = await TaskCacheService.find_existing_completed_task(db, canonical_url)
        if cached_task:
            cached_count += 1
            item = TaskItemResponse.model_validate(cached_task)
            item.is_cached = True
            response_items.append(item)
            continue

        # 3. In-flight check for active task
        active_task = await TaskCacheService.find_active_in_flight_task(db, canonical_url)
        if active_task:
            item = TaskItemResponse.model_validate(active_task)
            item.is_cached = False
            response_items.append(item)
            continue

        # 4. Create new task and enqueue
        new_task = Task(
            id=str(uuid.uuid4()),
            original_url=raw_url,
            canonical_url=canonical_url,
            status=TaskStatus.PENDING.value,
        )
        db.add(new_task)
        await db.commit()
        await db.refresh(new_task)

        # Enqueue for background worker
        enqueue_reel_analysis(new_task.id)
        created_count += 1

        item = TaskItemResponse.model_validate(new_task)
        item.is_cached = False
        response_items.append(item)

    return TaskBatchResponse(
        created_count=created_count,
        cached_count=cached_count,
        tasks=response_items,
    )


@router.get("", response_model=list[TaskItemResponse])
async def list_tasks(
    db: Annotated[AsyncSession, Depends(get_db)],
    ids: str | None = Query(
        default=None,
        description="Comma-separated task IDs for batch live polling",
    ),
    limit: int = Query(default=50, ge=1, le=100),
    task_status: str | None = Query(default=None, alias="status"),
):
    """
    Lists tasks. When `ids` parameter is passed, returns only those tasks
    for efficient frontend real-time polling.
    """
    stmt = select(Task).order_by(Task.created_at.desc())

    if ids:
        id_list = [i.strip() for i in ids.split(",") if i.strip()]
        if id_list:
            stmt = stmt.where(Task.id.in_(id_list))

    if task_status:
        stmt = stmt.where(Task.status == task_status)

    stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    tasks = result.scalars().all()

    return [TaskItemResponse.model_validate(t) for t in tasks]


@router.get("/{task_id}", response_model=TaskItemResponse)
async def get_task_detail(
    task_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Returns detailed information and analysis for a single task."""
    stmt = select(Task).where(Task.id == task_id)
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Задача '{task_id}' не найдена.",
        )

    return TaskItemResponse.model_validate(task)
