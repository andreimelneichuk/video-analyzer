import logging
import os
import shutil
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from src.services.extractor.ytdlp_extractor import YtDlpExtractor

logger = logging.getLogger(__name__)


@contextmanager
def temporary_reel_session(
    url: str, max_duration_sec: int = 180
) -> Generator[tuple[dict[str, Any], str, bool], None, None]:
    """
    Context manager managing the complete lifecycle of a downloaded video.
    Guarantees cleanup of the temporary folder and video files on exit or exception.

    Yields:
        (metrics_dict, video_path, has_audio)
    """
    extractor = YtDlpExtractor(max_duration_sec=max_duration_sec)
    metrics, video_path, has_audio = extractor.extract_info_and_download(url)
    temp_dir = os.path.dirname(video_path)

    try:
        yield metrics, video_path, has_audio
    finally:
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
                logger.debug("Successfully cleaned up temporary video directory: %s", temp_dir)
            except OSError as e:
                logger.warning("Failed to remove temporary directory %s: %s", temp_dir, e)
