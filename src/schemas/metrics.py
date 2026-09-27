from pydantic import BaseModel, ConfigDict


class ReelMetricsResponse(BaseModel):
    """
    Public representation of extracted Reel metrics.
    Preserves None (null) for metrics that are hidden or unavailable.
    """

    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    author: str | None = None
    upload_date: str | None = None

    model_config = ConfigDict(from_attributes=True)
