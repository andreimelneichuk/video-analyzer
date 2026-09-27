from enum import Enum, IntEnum


class TaskStatus(str, Enum):
    """Lifecycle status of a video analysis task."""

    PENDING = "PENDING"  # Enqueued, waiting for worker
    DOWNLOADING = "DOWNLOADING"  # Fetching metadata and downloading video
    ANALYZING = "ANALYZING"  # Running multimodal AI vision/audio analysis
    COMPLETED = "COMPLETED"  # Successfully finished with results
    FAILED = "FAILED"  # Graceful failure (private, deleted, timeout, invalid URL)


class IntegrationClass(IntEnum):
    """
    Skycoach integration class:
    0 — Nothing about Skycoach
    1 — Mention only (logo/brand named, but product is not advertised)
    2 — Product advertised (boosting, currency, raid carry, promo code, CTA)
    """

    NONE = 0
    MENTION = 1
    DIRECT_AD = 2


class BannerDefect(str, Enum):
    """
    Banner defect types matching Skycoach payout deduction guidelines:
    - cut_off_edge: Banner cut off on edge / logo clipped -> 20% deduction
    - too_small: Banner too small -> 30% deduction (or 20% if only slightly small)
    - overlapped_by_ui: Overlapped by UI buttons, captions, camera notch -> 20% deduction
    - not_visible: Banner not visible / fully obscured -> excluded (0 payout)
    """

    CUT_OFF_EDGE = "cut_off_edge"
    TOO_SMALL = "too_small"
    OVERLAPPED_BY_UI = "overlapped_by_ui"
    NOT_VISIBLE = "not_visible"
