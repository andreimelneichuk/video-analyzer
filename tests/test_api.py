from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.database import get_db
from src.main import app
from src.models import IntegrationAnalysis, IntegrationClass, ReelMetrics, Task, TaskStatus


@pytest_asyncio.fixture
async def api_client(db_session) -> AsyncGenerator[AsyncClient, None]:
    """Test client overriding database dependency with in-memory session."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_health_check(api_client: AsyncClient):
    """Verifies /health endpoint returns 200 OK."""
    response = await api_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_root_serves_html(api_client: AsyncClient):
    """Verifies that root URL serves the HTML UI."""
    response = await api_client.get("/")
    assert response.status_code == 200
    assert "Skycoach" in response.text
    assert "urlsInput" in response.text


@pytest.mark.asyncio
async def test_batch_create_validation(api_client: AsyncClient):
    """Verifies limits: empty batch and > 20 batch."""
    # Empty
    res_empty = await api_client.post("/api/tasks", json={"urls": []})
    assert res_empty.status_code == 422

    # > 20 URLs
    res_over = await api_client.post(
        "/api/tasks", json={"urls": [f"https://www.instagram.com/reel/p{i}/" for i in range(21)]}
    )
    assert res_over.status_code == 422


@pytest.mark.asyncio
async def test_batch_create_and_caching(api_client: AsyncClient, db_session):
    """
    Verifies that initial submission creates a task, and re-submitting the same
    canonical URL returns the cached result with is_cached=True.
    """
    url = "https://www.instagram.com/reel/DceO7gsR0w-/?utm_medium=copy"

    # 1. First submission (new task)
    res1 = await api_client.post("/api/tasks", json={"urls": [url]})
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["created_count"] == 1
    assert data1["cached_count"] == 0
    task_id = data1["tasks"][0]["id"]
    assert data1["tasks"][0]["is_cached"] is False
    assert data1["tasks"][0]["status"] == "PENDING"

    # 2. Simulate worker completing task in DB
    task = await db_session.get(Task, task_id)
    task.status = TaskStatus.COMPLETED.value
    task.metrics = ReelMetrics(
        task_id=task.id,
        views=1760000,
        likes=None,
        author="valorant_funzone",
    )
    task.analysis = IntegrationAnalysis(
        task_id=task.id,
        integration_class=IntegrationClass.DIRECT_AD,
        prominence_score=5,
        banner_duration_seconds=12.0,
        screen_percentage=15.0,
        has_voice_cta=True,
        has_text_cta=True,
        promo_code="SKY",
        defects=[],
        deduction_percent=0,
        payout_recommendation="Full payout",
        reasoning="Превосходная интеграция",
    )
    await db_session.commit()

    # 3. Second submission of the same URL (with different query param)
    url_duplicate = "https://www.instagram.com/reel/DceO7gsR0w-/?igsh=some_tracker"
    res2 = await api_client.post("/api/tasks", json={"urls": [url_duplicate]})
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["created_count"] == 0
    assert data2["cached_count"] == 1
    assert data2["tasks"][0]["id"] == task_id
    assert data2["tasks"][0]["is_cached"] is True
    assert data2["tasks"][0]["status"] == "COMPLETED"
    assert data2["tasks"][0]["metrics"]["views"] == 1760000


@pytest.mark.asyncio
async def test_get_tasks_polling(api_client: AsyncClient, db_session):
    """Verifies filtering tasks by comma-separated ?ids=... parameter."""
    task1 = Task(
        id="poll-1",
        original_url="u1",
        canonical_url="https://www.instagram.com/reel/u1/",
        status="PENDING",
    )
    task2 = Task(
        id="poll-2",
        original_url="u2",
        canonical_url="https://www.instagram.com/reel/u2/",
        status="PENDING",
    )
    db_session.add_all([task1, task2])
    await db_session.commit()

    res = await api_client.get("/api/tasks?ids=poll-1")
    assert res.status_code == 200
    tasks = res.json()
    assert len(tasks) == 1
    assert tasks[0]["id"] == "poll-1"


@pytest.mark.asyncio
async def test_export_csv(api_client: AsyncClient, db_session):
    """Verifies CSV export endpoint."""
    task = Task(
        id="export-1",
        original_url="https://www.instagram.com/reel/exp1/",
        canonical_url="https://www.instagram.com/reel/exp1/",
        status="COMPLETED",
    )
    task.metrics = ReelMetrics(task_id="export-1", views=200000, author="streamer")
    task.analysis = IntegrationAnalysis(
        task_id="export-1",
        integration_class=2,
        prominence_score=4,
        banner_duration_seconds=8.0,
        screen_percentage=10.0,
        defects=[],
        deduction_percent=0,
        payout_recommendation="Full payout",
        reasoning="Good video",
    )
    db_session.add(task)
    await db_session.commit()

    response = await api_client.get("/api/export/csv")
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "export-1" in response.text
    assert "streamer" in response.text


@pytest.mark.asyncio
async def test_retry_failed_task(api_client: AsyncClient, db_session):
    """Verifies a FAILED task is reset to PENDING with partial results dropped."""
    task = Task(
        id="retry-1",
        original_url="https://www.instagram.com/reel/DceO7gsR0w-/",
        canonical_url="https://www.instagram.com/reel/DceO7gsR0w-/",
        status=TaskStatus.FAILED.value,
        error_message="Внутренняя ошибка сервиса: timeout",
    )
    task.metrics = ReelMetrics(task_id="retry-1", views=100)
    db_session.add(task)
    await db_session.commit()

    res = await api_client.post("/api/tasks/retry-1/retry")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "PENDING"
    assert data["error_message"] is None
    assert data["metrics"] is None
    assert data["analysis"] is None


@pytest.mark.asyncio
async def test_retry_rejects_non_failed_and_invalid(api_client: AsyncClient, db_session):
    """Verifies retry is refused for missing, non-FAILED and invalid-URL tasks."""
    db_session.add_all(
        [
            Task(
                id="retry-pending",
                original_url="https://www.instagram.com/reel/p1/",
                canonical_url="https://www.instagram.com/reel/p1/",
                status=TaskStatus.PENDING.value,
            ),
            Task(
                id="retry-invalid",
                original_url="https://invalid-url-domain.com/not-a-reel",
                canonical_url="https://invalid-url-domain.com/not-a-reel",
                status=TaskStatus.FAILED.value,
                error_message="Некорректная ссылка",
            ),
        ]
    )
    await db_session.commit()

    assert (await api_client.post("/api/tasks/missing/retry")).status_code == 404
    assert (await api_client.post("/api/tasks/retry-pending/retry")).status_code == 409
    assert (await api_client.post("/api/tasks/retry-invalid/retry")).status_code == 422
