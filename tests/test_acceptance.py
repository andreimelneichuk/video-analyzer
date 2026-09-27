import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.database import get_db
from src.main import app
from src.models import IntegrationClass, Task, TaskStatus
from src.schemas.analysis import VlmRawObservation
from src.services.extractor.exceptions import (
    PrivateAccountError,
    ReelTooLongError,
    VideoNotFoundError,
)
from src.workers.tasks import async_process_reel_task


@pytest.fixture
def api_client(db_session):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    yield client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_acceptance_criteria_1_private_reel(db_session):
    """
    Приёмочный тест 1: Приватный ролик.
    Сервис не должен падать и должен внятно объяснить, что произошло.
    """
    url = "https://www.instagram.com/reel/privateAccountPost/"
    task = Task(id="accept-1-priv", original_url=url, canonical_url=url, status="PENDING")
    db_session.add(task)
    await db_session.commit()

    with patch("src.workers.tasks.YtDlpExtractor") as mock_extractor_cls:
        mock_ext = MagicMock()
        mock_ext.extract_info_and_download.side_effect = PrivateAccountError()
        mock_extractor_cls.return_value = mock_ext

        finished = await async_process_reel_task("accept-1-priv", db_session=db_session)

        assert finished.status == TaskStatus.FAILED.value
        assert "приватный" in finished.error_message.lower()


@pytest.mark.asyncio
async def test_acceptance_criteria_2_deleted_reel(db_session):
    """
    Приёмочный тест 2: Удалённый ролик (404).
    Сервис не должен падать и должен внятно объяснить причину.
    """
    url = "https://www.instagram.com/reel/deletedPost404/"
    task = Task(id="accept-2-del", original_url=url, canonical_url=url, status="PENDING")
    db_session.add(task)
    await db_session.commit()

    with patch("src.workers.tasks.YtDlpExtractor") as mock_extractor_cls:
        mock_ext = MagicMock()
        mock_ext.extract_info_and_download.side_effect = VideoNotFoundError()
        mock_extractor_cls.return_value = mock_ext

        finished = await async_process_reel_task("accept-2-del", db_session=db_session)

        assert finished.status == TaskStatus.FAILED.value
        assert "удален" in finished.error_message.lower()


@pytest.mark.asyncio
async def test_acceptance_criteria_3_soundless_reel(db_session):
    """
    Приёмочный тест 3: Ролик без звука.
    Модель корректно обрабатывает видеоряд, фиксирует has_voice_cta=False и завершается успехом.
    """
    temp_dir = tempfile.mkdtemp(prefix="soundless_")
    video_path = os.path.join(temp_dir, "silent.mp4")
    with open(video_path, "wb") as f:  # noqa: ASYNC230
        f.write(b"silent video")

    url = "https://www.instagram.com/reel/silentPost/"
    task = Task(id="accept-3-silent", original_url=url, canonical_url=url, status="PENDING")
    db_session.add(task)
    await db_session.commit()

    with (
        patch("src.workers.tasks.YtDlpExtractor") as mock_extractor_cls,
        patch("src.workers.tasks.VlmClient") as mock_vlm_cls,
    ):
        mock_ext = MagicMock()
        mock_ext.extract_info_and_download.return_value = (
            {"views": 182000, "author": "gamer"},
            video_path,
            False,  # has_audio = False!
        )
        mock_extractor_cls.return_value = mock_ext

        mock_vlm = MagicMock()
        mock_vlm.analyze_video = AsyncMock(
            return_value=VlmRawObservation(
                has_skycoach_mention=True,
                is_product_advertised=True,
                banner_duration_seconds=9.0,
                screen_percentage=11.0,
                has_voice_cta=False,  # Voice CTA is false
                has_text_cta=True,
                promo_code="SILENT",
                observed_defects=[],
                visual_observations="Баннер виден, звука нет.",
            )
        )
        mock_vlm_cls.return_value = mock_vlm

        finished = await async_process_reel_task("accept-3-silent", db_session=db_session)

        assert finished.status == TaskStatus.COMPLETED.value
        assert finished.analysis.has_voice_cta is False
        assert finished.analysis.has_text_cta is True
        assert finished.analysis.integration_class == IntegrationClass.DIRECT_AD


@pytest.mark.asyncio
async def test_acceptance_criteria_4_long_reel(db_session):
    """
    Приёмочный тест 4: Длинный ролик (> 180 сек).
    Воркер отсекает по лимиту времени и выставляет понятный статус FAILED.
    """
    url = "https://www.instagram.com/reel/longReel250s/"
    task = Task(id="accept-4-long", original_url=url, canonical_url=url, status="PENDING")
    db_session.add(task)
    await db_session.commit()

    with patch("src.workers.tasks.YtDlpExtractor") as mock_extractor_cls:
        mock_ext = MagicMock()
        mock_ext.extract_info_and_download.side_effect = ReelTooLongError(duration=250.0)
        mock_extractor_cls.return_value = mock_ext

        finished = await async_process_reel_task("accept-4-long", db_session=db_session)

        assert finished.status == TaskStatus.FAILED.value
        assert "250 сек" in finished.error_message


@pytest.mark.asyncio
async def test_acceptance_criteria_5_duplicate_link(api_client: AsyncClient, db_session):
    """
    Приёмочный тест 5: Одну и ту же ссылку дважды.
    Повторная отправка не запускает обработку заново, а отдаёт готовый результат (is_cached=True).
    """
    url = "https://www.instagram.com/reel/DcCCzPcR4fy/"

    # Pre-populate completed task in DB
    existing_task = Task(
        id="task-prev-completed",
        original_url=url,
        canonical_url=url,
        status="COMPLETED",
    )
    db_session.add(existing_task)
    await db_session.commit()

    # Send same link via API
    res = await api_client.post("/api/tasks", json={"urls": [url]})
    assert res.status_code == 200
    data = res.json()
    assert data["cached_count"] == 1
    assert data["tasks"][0]["id"] == "task-prev-completed"
    assert data["tasks"][0]["is_cached"] is True
    assert data["tasks"][0]["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_acceptance_criteria_6_invalid_link(api_client: AsyncClient):
    """
    Приёмочный тест 6: Невалидная ссылка.
    Сервис не должен падать и должен внятно объяснить, что формат некорректен.
    """
    invalid_url = "https://not-an-instagram-site.org/some/article"
    res = await api_client.post("/api/tasks", json={"urls": [invalid_url]})
    assert res.status_code == 200
    data = res.json()
    task = data["tasks"][0]
    assert task["status"] == "FAILED"
    assert "Некорректная ссылка" in task["error_message"]
