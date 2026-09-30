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
    assert (
        normalize_video_url(tt_url) == "https://www.tiktok.com/@streamer/video/7234567890123456789"
    )

    # Short share links must stay on vm.tiktok.com, otherwise TikTok answers 404
    assert normalize_video_url("https://vm.tiktok.com/ZGdxWt2fc/") == (
        "https://vm.tiktok.com/ZGdxWt2fc/"
    )


def test_normalize_profile_scoped_instagram_and_facebook_urls():
    """Link formats used in banner_review_examples.pdf."""
    assert (
        normalize_video_url("https://www.instagram.com/valorant_funzone/reel/DceO7gsR0w-/")
        == "https://www.instagram.com/reel/DceO7gsR0w-/"
    )

    assert normalize_video_url("https://www.facebook.com/share/r/19ZwChKjBV/?mibextid=x") == (
        "https://www.facebook.com/share/r/19ZwChKjBV/"
    )
    assert normalize_video_url("https://www.facebook.com/reel/1723178395470033") == (
        "https://www.facebook.com/reel/1723178395470033"
    )


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
        "like_count": None,  # Hidden likes!
        "comment_count": None,  # Hidden comments!
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
    mock_ydl.extract_info.side_effect = yt_dlp.utils.DownloadError(
        "The handshake operation timed out"
    )

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


@patch("src.services.extractor.ytdlp_extractor.time.sleep")
@patch("yt_dlp.YoutubeDL")
def test_extractor_retries_transient_ssl_error(mock_ydl_cls, _sleep):
    """A dropped TLS connection is retried instead of failing the task."""
    mock_ydl = MagicMock()
    mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.side_effect = [
        yt_dlp.utils.DownloadError(
            "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol"
        ),
        None,
    ]

    with pytest.raises(VideoNotFoundError):
        YtDlpExtractor().extract_info_and_download("https://www.instagram.com/reel/flaky/")
    assert mock_ydl.extract_info.call_count == 2


def test_cookies_only_for_instagram(tmp_path, monkeypatch):
    """Cookies are passed to Instagram only; YouTube breaks when logged in."""
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    monkeypatch.setattr(
        "src.services.extractor.ytdlp_extractor.settings.YTDLP_COOKIES_FILE", str(cookies)
    )
    extractor = YtDlpExtractor()
    assert "cookiefile" in extractor._get_ydl_options(url="https://www.instagram.com/reel/x/")
    assert "cookiefile" not in extractor._get_ydl_options(url="https://www.youtube.com/shorts/x")


def test_instagram_play_count_from_media_info():
    """Reel plays are read from the media info API when yt-dlp has no view_count."""
    response = MagicMock()
    response.read.return_value = b'{"items": [{"play_count": 7883}]}'
    ydl = MagicMock()
    ydl.urlopen.return_value.__enter__.return_value = response

    assert YtDlpExtractor._fetch_instagram_play_count(ydl, "DbQGs9HMcOQ") == 7883
    requested_url = ydl.urlopen.call_args[0][0].url
    assert requested_url == "https://i.instagram.com/api/v1/media/3949686350758921104/info/"

    ydl.urlopen.side_effect = OSError("blocked")
    assert YtDlpExtractor._fetch_instagram_play_count(ydl, "DbQGs9HMcOQ") is None


def test_ui_session_overrides_mounted_cookies(tmp_path, monkeypatch):
    """Session saved from the UI takes priority; yt-dlp always gets a copy."""
    mounted = tmp_path / "mounted.txt"
    mounted.write_text("# Netscape HTTP Cookie File\n")
    ui = tmp_path / "ig_session.txt"
    ui.write_text("# Netscape HTTP Cookie File\n")
    monkeypatch.setattr("src.config.settings.YTDLP_COOKIES_FILE", str(mounted))
    monkeypatch.setattr("src.config.settings.IG_SESSION_FILE", str(ui))

    target = tmp_path / "work"
    target.mkdir()
    opts = YtDlpExtractor()._get_ydl_options(
        target_dir=str(target), url="https://www.instagram.com/reel/x/"
    )
    assert opts["cookiefile"] == str(target / "cookies.txt")

    ui.unlink()
    from src.services.extractor import ig_session

    assert ig_session.active_cookies_file() == str(mounted)


def test_build_cookies_text_from_sessionid_and_export():
    from src.services.extractor.ig_session import InvalidCookiesError, build_cookies_text

    text = build_cookies_text(sessionid="12345%3AabcDEF%3A7")
    assert "\tsessionid\t12345%3AabcDEF%3A7" in text
    assert "\tds_user_id\t12345" in text

    export = (
        "# Netscape HTTP Cookie File\n"
        ".youtube.com\tTRUE\t/\tTRUE\t0\tSID\tyt\n"
        ".instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tig\n"
    )
    only_ig = build_cookies_text(cookies_txt=export)
    assert "youtube" not in only_ig and "\tsessionid\tig" in only_ig

    with pytest.raises(InvalidCookiesError):
        build_cookies_text(cookies_txt="# Netscape HTTP Cookie File\n")


@patch("src.services.extractor.ytdlp_extractor.yt_dlp.YoutubeDL")
def test_known_invalid_session_fails_fast(mock_ydl_cls, tmp_path, monkeypatch):
    """A session already rejected by Instagram isn't used for new downloads."""
    from src.services.extractor import ig_session
    from src.services.extractor.exceptions import AuthRequiredError

    cookies = tmp_path / "ig_session.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    monkeypatch.setattr("src.config.settings.IG_SESSION_FILE", str(cookies))
    ig_session.mark_invalid("dead")

    with pytest.raises(AuthRequiredError):
        YtDlpExtractor().extract_info_and_download("https://www.instagram.com/reel/x/")
    mock_ydl_cls.assert_not_called()
