import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import IntegrationClass, Task, TaskStatus
from src.schemas.analysis import VlmRawObservation
from src.services.cache_service import TaskCacheService
from src.services.extractor.exceptions import PrivateAccountError
from src.workers.tasks import async_process_reel_task


@pytest.mark.asyncio
async def test_cache_service_lookups(db_session: AsyncSession):
    """Verifies that TaskCacheService accurately finds completed and in-flight tasks."""
    url = "https://www.instagram.com/reel/DceO7gsR0w-/"

    # 1. Initially empty
    assert await TaskCacheService.find_existing_completed_task(db_session, url) is None
    assert await TaskCacheService.find_active_in_flight_task(db_session, url) is None

    # 2. Add in-flight task
    task = Task(
        id="task-inflight-1",
        original_url=url,
        canonical_url=url,
        status=TaskStatus.DOWNLOADING.value,
    )
    db_session.add(task)
    await db_session.commit()

    assert await TaskCacheService.find_existing_completed_task(db_session, url) is None
    active = await TaskCacheService.find_active_in_flight_task(db_session, url)
    assert active is not None
    assert active.id == "task-inflight-1"

    # 3. Mark completed
    task.status = TaskStatus.COMPLETED.value
    await db_session.commit()

    completed = await TaskCacheService.find_existing_completed_task(db_session, url)
    assert completed is not None
    assert completed.id == "task-inflight-1"
    assert await TaskCacheService.find_active_in_flight_task(db_session, url) is None


@pytest.mark.asyncio
@patch("src.workers.tasks.YtDlpExtractor")
@patch("src.workers.tasks.VlmClient")
async def test_worker_pipeline_success(mock_vlm_cls, mock_extractor_cls, db_session: AsyncSession):
    """
    Verifies full background worker pipeline:
    download -> extract -> analyze -> evaluate rules -> save COMPLETED -> cleanup
    """
    temp_dir = tempfile.mkdtemp(prefix="worker_test_")
    video_path = os.path.join(temp_dir, "test.mp4")
    with open(video_path, "wb") as f:  # noqa: ASYNC230
        f.write(b"video bytes")

    # Mock extractor
    mock_extractor = MagicMock()
    mock_extractor.extract_info_and_download.return_value = (
        {
            "views": 448000,
            "likes": 12000,
            "comments": None,  # hidden comments!
            "author": "valorant_funzone",
            "upload_date": "2026-09-18",
        },
        video_path,
        True,
    )
    mock_extractor_cls.return_value = mock_extractor

    # Mock VLM
    mock_vlm = MagicMock()
    mock_vlm.analyze_video = AsyncMock(
        return_value=VlmRawObservation(
            has_skycoach_mention=True,
            is_product_advertised=True,
            banner_duration_seconds=11.0,
            screen_percentage=13.0,
            has_voice_cta=True,
            has_text_cta=True,
            promo_code="VALORANT",
            observed_defects=[],
            visual_observations="Отличный баннер по центру.",
        )
    )
    mock_vlm_cls.return_value = mock_vlm

    # Create task
    task = Task(
        id="task-success-1",
        original_url="https://www.instagram.com/reel/DcUEhNgx8Il/?utm_source=ig",
        canonical_url="https://www.instagram.com/reel/DcUEhNgx8Il/",
        status=TaskStatus.PENDING.value,
    )
    db_session.add(task)
    await db_session.commit()

    # Run worker pipeline
    finished_task = await async_process_reel_task("task-success-1", db_session=db_session)

    assert finished_task is not None
    assert finished_task.status == TaskStatus.COMPLETED.value
    assert finished_task.metrics is not None
    assert finished_task.metrics.views == 448000
    assert finished_task.metrics.comments is None  # Preserved as None!
    assert finished_task.analysis is not None
    assert finished_task.analysis.integration_class == IntegrationClass.DIRECT_AD
    assert finished_task.analysis.prominence_score >= 4
    assert finished_task.analysis.deduction_percent == 0
    assert "Full payout" in finished_task.analysis.payout_recommendation

    # Verify temp dir was cleaned up
    assert not os.path.exists(temp_dir)


@pytest.mark.asyncio
@patch("src.workers.tasks.YtDlpExtractor")
async def test_worker_pipeline_extractor_failure(mock_extractor_cls, db_session: AsyncSession):
    """
    Verifies that when extractor raises a typed error (e.g. PrivateAccountError),
    the worker marks the task FAILED with user-friendly message without crashing.
    """
    mock_extractor = MagicMock()
    mock_extractor.extract_info_and_download.side_effect = PrivateAccountError()
    mock_extractor_cls.return_value = mock_extractor

    task = Task(
        id="task-private-fail",
        original_url="https://www.instagram.com/reel/private123/",
        canonical_url="https://www.instagram.com/reel/private123/",
        status=TaskStatus.PENDING.value,
    )
    db_session.add(task)
    await db_session.commit()

    failed_task = await async_process_reel_task("task-private-fail", db_session=db_session)

    assert failed_task is not None
    assert failed_task.status == TaskStatus.FAILED.value
    assert "приватный" in failed_task.error_message.lower()
