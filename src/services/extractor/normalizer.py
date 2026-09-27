import re

INSTAGRAM_REEL_PATTERN = re.compile(
    r"(?:https?:\/\/)?(?:www\.)?instagram\.com\/(?:reel|reels|p)\/([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)

YOUTUBE_SHORTS_PATTERN = re.compile(
    r"(?:https?:\/\/)?(?:www\.)?youtube\.com\/shorts\/([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)

TIKTOK_PATTERN = re.compile(
    r"(?:https?:\/\/)?(?:www\.|vm\.)?tiktok\.com\/(?:@[\w.-]+\/video\/|v\/|t\/)?([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)


def normalize_video_url(url: str) -> str | None:
    """
    Normalizes a video URL to canonical form, stripping tracking queries and hashes.
    Supports Instagram Reels, YouTube Shorts, and TikTok (as present in Skycoach dataset).
    Returns canonical URL or None if URL is invalid.
    """
    if not url or not isinstance(url, str):
        return None

    cleaned = url.strip()

    # Match Instagram Reel / Post
    ig_match = INSTAGRAM_REEL_PATTERN.search(cleaned)
    if ig_match:
        shortcode = ig_match.group(1)
        return f"https://www.instagram.com/reel/{shortcode}/"

    # Match YouTube Shorts
    yt_match = YOUTUBE_SHORTS_PATTERN.search(cleaned)
    if yt_match:
        video_id = yt_match.group(1)
        return f"https://www.youtube.com/shorts/{video_id}"

    # Match TikTok
    tt_match = TIKTOK_PATTERN.search(cleaned)
    if tt_match:
        video_id = tt_match.group(1)
        return f"https://www.tiktok.com/video/{video_id}"

    return None
