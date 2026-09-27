from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base

if TYPE_CHECKING:
    from src.models.task import Task


class ReelMetrics(Base):
    """
    Extracted social media metrics for an Instagram Reel / Video post.
    CRITICAL: Missing / hidden metrics (e.g. author hid like counts) MUST be None (NULL),
    NOT 0, so the manager sees 'N/A' rather than false zero metrics.
    """

    __tablename__ = "reel_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), unique=True, index=True
    )

    views: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    likes: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    comments: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    upload_date: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None)

    # Relationship back to parent Task
    task: Mapped["Task"] = relationship("Task", back_populates="metrics")
