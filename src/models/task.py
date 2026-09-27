import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base
from src.models.analysis import IntegrationAnalysis
from src.models.enums import TaskStatus
from src.models.metrics import ReelMetrics


class Task(Base):
    """
    Main entity representing a single Instagram Reel processing task.
    """

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    # URL details
    original_url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str] = mapped_column(
        String(512),
        index=True,
        nullable=False,
    )

    # Lifecycle state
    status: Mapped[str] = mapped_column(
        String(32),
        default=TaskStatus.PENDING.value,
        index=True,
        nullable=False,
    )

    # Error message for failed tasks
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    # Associated metrics (views, likes, comments, etc.)
    metrics: Mapped[ReelMetrics | None] = relationship(
        "ReelMetrics",
        back_populates="task",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    # Associated AI & Rule analysis
    analysis: Mapped[IntegrationAnalysis | None] = relationship(
        "IntegrationAnalysis",
        back_populates="task",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin",
    )
