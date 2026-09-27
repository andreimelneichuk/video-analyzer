from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.models.enums import TaskStatus
from src.schemas.analysis import IntegrationAnalysisResponse
from src.schemas.metrics import ReelMetricsResponse


class TaskBatchCreateRequest(BaseModel):
    """Request payload for batch video processing."""

    urls: list[str] = Field(
        ...,
        min_length=1,
        max_length=20,
        description="List of Instagram Reels or YouTube Shorts URLs to analyze (up to 20 at a time)",
    )


class TaskItemResponse(BaseModel):
    """Response representation of a single analysis task."""

    id: str
    original_url: str
    canonical_url: str
    status: TaskStatus
    error_message: str | None = None
    is_cached: bool = False
    created_at: datetime
    updated_at: datetime
    metrics: ReelMetricsResponse | None = None
    analysis: IntegrationAnalysisResponse | None = None

    model_config = ConfigDict(from_attributes=True)


class TaskBatchResponse(BaseModel):
    """Response payload returned when submitting a batch of URLs."""

    created_count: int
    cached_count: int
    tasks: list[TaskItemResponse]
