import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    BannerDefect,
    IntegrationAnalysis,
    IntegrationClass,
    ReelMetrics,
    Task,
    TaskStatus,
)
from src.schemas import TaskBatchCreateRequest


@pytest.mark.asyncio
async def test_task_creation_and_null_metrics(db_session: AsyncSession):
    """
    Verifies that a Task can be created and that missing ReelMetrics
    are strictly saved and retrieved as None (NULL), NOT 0.
    """
    task_id = str(uuid.uuid4())
    task = Task(
        id=task_id,
        original_url="https://www.instagram.com/reel/DceO7gsR0w-/?utm_source=test",
        canonical_url="https://www.instagram.com/reel/DceO7gsR0w-/",
        status=TaskStatus.PENDING.value,
    )
    db_session.add(task)
    await db_session.commit()

    # Add metrics with hidden likes and comments (None)
    metrics = ReelMetrics(
        task_id=task_id,
        views=1760000,
        likes=None,  # Author hid likes! Must stay None, not 0
        comments=None,  # Comments disabled! Must stay None, not 0
        author="valorant_funzone",
        upload_date="2026-09-20",
    )
    db_session.add(metrics)
    await db_session.commit()

    # Query back
    stmt = select(Task).where(Task.id == task_id)
    result = await db_session.execute(stmt)
    retrieved_task = result.scalar_one()

    assert retrieved_task.id == task_id
    assert retrieved_task.status == TaskStatus.PENDING.value
    assert retrieved_task.metrics is not None
    assert retrieved_task.metrics.views == 1760000
    assert retrieved_task.metrics.likes is None
    assert retrieved_task.metrics.comments is None
    assert retrieved_task.metrics.author == "valorant_funzone"


@pytest.mark.asyncio
async def test_task_with_analysis(db_session: AsyncSession):
    """
    Verifies saving and retrieving IntegrationAnalysis with defects and payout recommendations.
    """
    task_id = str(uuid.uuid4())
    task = Task(
        id=task_id,
        original_url="https://www.instagram.com/reel/Dcdl9dgSEHd/",
        canonical_url="https://www.instagram.com/reel/Dcdl9dgSEHd/",
        status=TaskStatus.COMPLETED.value,
    )
    db_session.add(task)
    await db_session.commit()

    analysis = IntegrationAnalysis(
        task_id=task_id,
        integration_class=IntegrationClass.DIRECT_AD,
        prominence_score=4,
        banner_duration_seconds=9.5,
        screen_percentage=12.0,
        has_voice_cta=False,
        has_text_cta=True,
        promo_code="VALFUN",
        defects=[BannerDefect.CUT_OFF_EDGE.value],
        deduction_percent=20,
        payout_recommendation="20% deduction: Banner cut off on edge",
        reasoning="Баннер частично обрезан левым краем кадра. Рекомендуется удержание 20%.",
    )
    db_session.add(analysis)
    await db_session.commit()

    stmt = select(Task).where(Task.id == task_id)
    result = await db_session.execute(stmt)
    retrieved = result.scalar_one()

    assert retrieved.analysis is not None
    assert retrieved.analysis.integration_class == IntegrationClass.DIRECT_AD
    assert retrieved.analysis.prominence_score == 4
    assert retrieved.analysis.defects == ["cut_off_edge"]
    assert retrieved.analysis.deduction_percent == 20
    assert retrieved.analysis.promo_code == "VALFUN"


def test_batch_url_limit_validation():
    """
    Verifies that TaskBatchCreateRequest strictly enforces 1 to 20 URLs limit.
    """
    # 0 URLs -> error
    with pytest.raises(ValidationError):
        TaskBatchCreateRequest(urls=[])

    # 20 URLs -> valid
    valid_batch = [f"https://www.instagram.com/reel/post{i}/" for i in range(20)]
    req = TaskBatchCreateRequest(urls=valid_batch)
    assert len(req.urls) == 20

    # 21 URLs -> error
    invalid_batch = [f"https://www.instagram.com/reel/post{i}/" for i in range(21)]
    with pytest.raises(ValidationError):
        TaskBatchCreateRequest(urls=invalid_batch)
