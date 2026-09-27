import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
import yt_dlp

from src.services.extractor import (
    ExtractionTimeoutError,
    PrivateAccountError,
    ReelTooLongError,
    VideoNotFoundError,
    YtDlpExtractor,
    normalize_video_url,
    temporary_reel_session,
)


def test_normalize_instagram_urls():
    """Verifies that Instagram links are normalized to canonical format."""
    # Standard reel
    url1 = "https://www.instagram.com/reel/DceO7gsR0w-/"
    assert normalize_video_url(url1) == "https://www.instagram.com/reel/DceO7gsR0w-/"

    # Reel with tracking query params
    url2 = "https://instagram.com/reel/DceO7gsR0w-/?utm_source=ig_web&igsh=XYZ123#frag"
    assert normalize_video_url(url2) == "https://www.instagram.com/reel/DceO7gsR0w-/"

    # Post /p/ link
    url3 = "https://www.instagram.com/p/Db3C60btk6E/"
    assert normalize_video_url(url3) == "https://www.instagram.com/reel/Db3C60btk6E/"


def test_normalize_youtube_and_tiktok_urls():
    """Verifies YouTube Shorts and TikTok normalization from banner_review_examples."""
    yt_url = "https://youtube.com/shorts/L3wbACRc_v0?si=test123"
    assert normalize_video_url(yt_url) == "https://www.youtube.com/shorts/L3wbACRc_v0"

    tt_url = "https://www.tiktok.com/@streamer/video/7234567890123456789?is_from_webapp=1"
    assert normalize_video_url(tt_url) == "https://www.tiktok.com/video/7234567890123456789"


def test_normalize_invalid_urls():
    """Verifies that malformed or non-supported URLs return None."""
    assert normalize_video_url("not_a_url") is None
    assert normalize_video_url("https://google.com/search?q=skycoach") is None
    assert normalize_video_url("") is None
    assert normalize_video_url(None) is None


@patch("yt_dlp.YoutubeDL")
def test_extractor_preserves_none_metrics(mock_ydl_cls):
    """
    Verifies that missing views/likes/comments are saved strictly as None,
    not 0, ensuring user sees 'N/A'.
    """
    mock_ydl = MagicMock()
    mock_ydl_cls.return_value.__enter__.return_value = mock_ydl

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_file:
        temp_file_name = temp_file.name

    mock_ydl.extract_info.return_value = {
        "view_count": 529000,
        "like_count": None,        # Hidden likes!
        "comment_count": None,     # Hidden comments!
        "uploader": "valorant_funzone",
        "upload_date": "20260920",
        "duration": 15.0,
        "acodec": "mp4a.40.2",
    }
    mock_ydl.prepare_filename.return_value = temp_file_name

    try:
        extractor = YtDlpExtractor()
        metrics, video_path, has_audio = extractor.extract_info_and_download(
            "https://www.instagram.com/reel/Db3C60btk6E/"
        )

        assert metrics["views"] == 529000
        assert metrics["likes"] is None
        assert metrics["comments"] is None
        assert metrics["author"] == "valorant_funzone"
        assert metrics["upload_date"] == "2026-09-20"
        assert has_audio is True
        assert video_path == temp_file_name
    finally:
        if os.path.exists(temp_file_name):
            os.remove(temp_file_name)


@patch("yt_dlp.YoutubeDL")
def test_extractor_private_account_error(mock_ydl_cls):
    """Verifies handling of private accounts."""
    mock_ydl = MagicMock()
    mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.side_effect = yt_dlp.utils.DownloadError(
        "ERROR: This account is private. Log in to view this post."
    )

    extractor = YtDlpExtractor()
    with pytest.raises(PrivateAccountError) as exc_info:
        extractor.extract_info_and_download("https://www.instagram.com/reel/private123/")

    assert "приватный" in exc_info.value.user_friendly_message.lower()


@patch("yt_dlp.YoutubeDL")
def test_extractor_video_not_found_error(mock_ydl_cls):
    """Verifies handling of deleted / 404 posts."""
    mock_ydl = MagicMock()
    mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.side_effect = yt_dlp.utils.DownloadError("HTTP Error 404: Not Found")

    extractor = YtDlpExtractor()
    with pytest.raises(VideoNotFoundError) as exc_info:
        extractor.extract_info_and_download("https://www.instagram.com/reel/deleted404/")

    assert "не найден или был удален" in exc_info.value.user_friendly_message.lower()


@patch("yt_dlp.YoutubeDL")
def test_extractor_timeout_error(mock_ydl_cls):
    """Verifies handling of handshake / network timeouts."""
    mock_ydl = MagicMock()
    mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.side_effect = yt_dlp.utils.DownloadError("The handshake operation timed out")

    extractor = YtDlpExtractor()
    with pytest.raises(ExtractionTimeoutError) as exc_info:
        extractor.extract_info_and_download("https://www.instagram.com/reel/timeoutReel/")

    assert "таймаут" in exc_info.value.user_friendly_message.lower()


@patch("yt_dlp.YoutubeDL")
def test_extractor_reel_too_long_error(mock_ydl_cls):
    """Verifies rejection of overly long videos (> 180s)."""
    mock_ydl = MagicMock()
    mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.return_value = {
        "duration": 320.0,
        "view_count": 100,
    }

    extractor = YtDlpExtractor(max_duration_sec=180)
    with pytest.raises(ReelTooLongError) as exc_info:
        extractor.extract_info_and_download("https://www.instagram.com/reel/longReel/")

    assert "320 сек" in exc_info.value.user_friendly_message
    assert "180 сек" in exc_info.value.user_friendly_message


@patch("src.services.extractor.session.YtDlpExtractor")
def test_temporary_reel_session_cleanup(mock_extractor_cls):
    """Verifies that temporary directory and video files are deleted after exit."""
    temp_dir = tempfile.mkdtemp(prefix="test_cleanup_")
    test_video_path = os.path.join(temp_dir, "video.mp4")
    with open(test_video_path, "wb") as f:
        f.write(b"dummy video data")

    mock_instance = MagicMock()
    mock_instance.extract_info_and_download.return_value = (
        {"views": 1000},
        test_video_path,
        True,
    )
    mock_extractor_cls.return_value = mock_instance

    assert os.path.exists(temp_dir)
    assert os.path.exists(test_video_path)

    with temporary_reel_session("https://www.instagram.com/reel/test/") as (m, path, has_audio):
        assert m["views"] == 1000
        assert os.path.exists(path)
        assert has_audio is True

    # After exiting context manager, temp_dir must be deleted
    assert not os.path.exists(temp_dir)
