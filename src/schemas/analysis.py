from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.models.enums import BannerDefect, IntegrationClass


class VlmRawObservation(BaseModel):
    """
    Structured raw perceptual observations returned by the multimodal VLM model.
    These observations are fed into the deterministic SkycoachRuleEngine.
    """

    has_skycoach_mention: bool = Field(
        description="Whether any Skycoach logo, text, or brand name is present in audio or visual track",
    )
    is_product_advertised: bool = Field(
        description="Whether a specific Skycoach service/product (game boosting, currency, raid carry) is promoted",
    )
    has_correct_logo: bool = Field(
        default=True,
        description="Whether the official Skycoach logo is present. False if a different/foreign logo is shown",
    )
    banner_duration_seconds: float = Field(
        ge=0.0,
        description="Total duration in seconds the Skycoach banner or logo was visible in frame",
    )
    screen_percentage: float = Field(
        ge=0.0,
        le=100.0,
        description="Estimated percentage of screen area occupied by the banner/logo (0.0 to 100.0)",
    )
    banner_bbox: list[float] | None = Field(
        default=None,
        description=(
            "Banner bounding box [x_min, y_min, x_max, y_max] on the frame where it is largest, "
            "normalized to 0-1000. When valid, screen_percentage is computed from it."
        ),
    )
    has_voice_cta: bool = Field(
        description="Whether the creator verbally delivered a call to action or promoted Skycoach",
    )
    has_text_cta: bool = Field(
        description="Whether there is an on-screen text call to action, website link, or promo code",
    )
    promo_code: str | None = Field(
        default=None,
        description="Extracted promo code text if present (e.g., 'VALFUN')",
    )
    observed_defects: list[BannerDefect] = Field(
        default_factory=list,
        description="List of observed visual defects according to Skycoach banner guidelines",
    )
    visual_observations: str = Field(
        description="Concise description of the banner placement, visual clarity, and game context",
    )

    @model_validator(mode="after")
    def _area_from_bbox(self) -> "VlmRawObservation":
        """
        VLMs are unreliable at guessing area percentages (a ~2.4% banner was
        reported as 4.5%), but localize boxes well. Derive the area from the box.
        """
        bbox = self.banner_bbox
        if not bbox or len(bbox) != 4:
            return self
        x1, y1, x2, y2 = bbox
        # Accept 0-1 fractions as well as the requested 0-1000 scale
        scale = 1.0 if max(bbox) <= 1.0 else 1000.0
        width = (x2 - x1) / scale
        height = (y2 - y1) / scale
        if not (0.0 < width <= 1.0 and 0.0 < height <= 1.0):
            return self
        self.screen_percentage = round(width * height * 100.0, 2)
        return self


class IntegrationAnalysisResponse(BaseModel):
    """
    Public representation of the evaluated ad integration and payout recommendation.
    """

    integration_class: IntegrationClass
    prominence_score: int
    has_correct_logo: bool = True
    banner_duration_seconds: float
    screen_percentage: float
    has_voice_cta: bool
    has_text_cta: bool
    promo_code: str | None = None
    defects: list[str] = Field(default_factory=list)
    deduction_percent: int
    payout_recommendation: str
    reasoning: str

    model_config = ConfigDict(from_attributes=True)
