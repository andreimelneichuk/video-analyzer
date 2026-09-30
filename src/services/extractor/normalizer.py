import re

# Profile-scoped links are common too: instagram.com/<username>/reel/<code>/
INSTAGRAM_REEL_PATTERN = re.compile(
    r"(?:https?:\/\/)?(?:www\.)?instagram\.com\/(?:[\w.]+\/)?(?:reel|reels|p)\/([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)

YOUTUBE_SHORTS_PATTERN = re.compile(
    r"(?:https?:\/\/)?(?:www\.)?youtube\.com\/shorts\/([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)

TIKTOK_VIDEO_PATTERN = re.compile(
    r"(?:https?:\/\/)?(?:www\.)?tiktok\.com\/@([\w.-]*)\/video\/(\d+)",
    re.IGNORECASE,
)

# Short share links only resolve through their own host, so they are kept as-is
TIKTOK_SHORT_PATTERN = re.compile(
    r"(?:https?:\/\/)?(vm|vt)\.tiktok\.com\/([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)

FACEBOOK_SHARE_PATTERN = re.compile(
    r"(?:https?:\/\/)?(?:www\.|m\.)?facebook\.com\/share\/(r|v)\/([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)

FACEBOOK_REEL_PATTERN = re.compile(
    r"(?:https?:\/\/)?(?:www\.|m\.)?facebook\.com\/(?:reel\/|watch\/?\?v=)(\d+)",
    re.IGNORECASE,
)


def normalize_video_url(url: str) -> str | None:
    """
    Normalizes a video URL to canonical form, stripping tracking queries and hashes.
    Supports Instagram Reels, YouTube Shorts, TikTok and Facebook Reels
    (all present in the Skycoach banner review dataset).
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
    tt_match = TIKTOK_VIDEO_PATTERN.search(cleaned)
    if tt_match:
        username, video_id = tt_match.groups()
        return f"https://www.tiktok.com/@{username}/video/{video_id}"

    tt_short_match = TIKTOK_SHORT_PATTERN.search(cleaned)
    if tt_short_match:
        host, code = tt_short_match.groups()
        return f"https://{host.lower()}.tiktok.com/{code}/"

    # Match Facebook Reels
    fb_share_match = FACEBOOK_SHARE_PATTERN.search(cleaned)
    if fb_share_match:
        kind, code = fb_share_match.groups()
        return f"https://www.facebook.com/share/{kind.lower()}/{code}/"

    fb_reel_match = FACEBOOK_REEL_PATTERN.search(cleaned)
    if fb_reel_match:
        return f"https://www.facebook.com/reel/{fb_reel_match.group(1)}"

    return None
