from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base
from src.models.enums import IntegrationClass

if TYPE_CHECKING:
    from src.models.task import Task


class IntegrationAnalysis(Base):
    """
    Detailed AI and Rule-Engine analysis results of a Skycoach ad integration.
    """

    __tablename__ = "integration_analysis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), unique=True, index=True
    )

    # Classification (0, 1, 2)
    integration_class: Mapped[int] = mapped_column(
        Integer, default=IntegrationClass.NONE, nullable=False
    )

    # Visual ad prominence score (1 to 5)
    prominence_score: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Quantitative characteristics
    banner_duration_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    screen_percentage: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    has_voice_cta: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_text_cta: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    promo_code: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)

    # Skycoach banner review defects list (JSON array of strings)
    defects: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    # Payout and deduction verdict according to Skycoach policies
    deduction_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    payout_recommendation: Mapped[str] = mapped_column(
        String(255), default="Full payout (No deduction)", nullable=False
    )

    # Human-readable structured explanation for influence managers
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)

    # Relationship back to parent Task
    task: Mapped["Task"] = relationship("Task", back_populates="analysis")
