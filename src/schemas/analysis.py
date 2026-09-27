from pydantic import BaseModel, ConfigDict, Field

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
    banner_duration_seconds: float = Field(
        ge=0.0,
        description="Total duration in seconds the Skycoach banner or logo was visible in frame",
    )
    screen_percentage: float = Field(
        ge=0.0,
        le=100.0,
        description="Estimated percentage of screen area occupied by the banner/logo (0.0 to 100.0)",
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


class IntegrationAnalysisResponse(BaseModel):
    """
    Public representation of the evaluated ad integration and payout recommendation.
    """

    integration_class: IntegrationClass
    prominence_score: int
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
