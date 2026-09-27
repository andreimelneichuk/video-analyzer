from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import TaskStatus
from src.models.task import Task


class TaskCacheService:
    """
    Service providing caching and deduplication lookups for Instagram Reel tasks.
    Ensures that re-submitted URLs return cached results instantly without re-processing.
    """

    @staticmethod
    async def find_existing_completed_task(
        session: AsyncSession, canonical_url: str
    ) -> Task | None:
        """
        Finds the most recent successfully completed analysis task for the canonical URL.
        """
        stmt = (
            select(Task)
            .where(
                and_(
                    Task.canonical_url == canonical_url,
                    Task.status == TaskStatus.COMPLETED.value,
                )
            )
            .order_by(Task.created_at.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def find_active_in_flight_task(session: AsyncSession, canonical_url: str) -> Task | None:
        """
        Finds an active task currently in progress for the canonical URL to prevent
        duplicate concurrent worker jobs.
        """
        active_statuses = [
            TaskStatus.PENDING.value,
            TaskStatus.DOWNLOADING.value,
            TaskStatus.ANALYZING.value,
        ]
        stmt = (
            select(Task)
            .where(
                and_(
                    Task.canonical_url == canonical_url,
                    Task.status.in_(active_statuses),
                )
            )
            .order_by(Task.created_at.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
