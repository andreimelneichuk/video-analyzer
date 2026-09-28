import asyncio
import logging
import os
import shutil

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import AsyncSessionLocal
from src.models import IntegrationAnalysis, ReelMetrics, Task, TaskStatus
from src.services.ai.vlm_client import VlmClient
from src.services.extractor.exceptions import ExtractorError
from src.services.extractor.ytdlp_extractor import YtDlpExtractor
from src.services.rules.engine import SkycoachRuleEngine

logger = logging.getLogger("worker")


def process_reel_task(task_id: str) -> None:
    """
    Entry point for RQ background worker. Runs the async processing pipeline.
    """
    asyncio.run(async_process_reel_task(task_id))


async def async_process_reel_task(
    task_id: str, db_session: AsyncSession | None = None
) -> Task | None:
    """
    Asynchronous implementation of the video processing pipeline:
    1. Update status to DOWNLOADING
    2. Extract metadata & download video stream (yt-dlp)
    3. Update status to ANALYZING
    4. Run multimodal VLM evaluation (Gemini 2.5 / Qwen Omni)
    5. Run deterministic SkycoachRuleEngine (deductions calculation)
    6. Save metrics and analysis to database with status COMPLETED
    7. Clean up temporary video files
    """
    if db_session is not None:
        return await _execute_task_pipeline(task_id, db_session)

    async with AsyncSessionLocal() as session:
        return await _execute_task_pipeline(task_id, session)


async def _execute_task_pipeline(task_id: str, session: AsyncSession) -> Task | None:
    stmt = select(Task).where(Task.id == task_id)
    result = await session.execute(stmt)
    task = result.scalar_one_or_none()

    if not task:
        logger.error("Task %s not found in database", task_id)
        return None

    # Step 1: Transition to DOWNLOADING
    task.status = TaskStatus.DOWNLOADING.value
    await session.commit()
    logger.info("Task %s: status -> DOWNLOADING (%s)", task_id, task.canonical_url)

    video_path: str | None = None
    temp_dir: str | None = None

    try:
        # Step 2: Download media and extract metrics
        extractor = YtDlpExtractor()
        metrics_dict, video_path, has_audio = extractor.extract_info_and_download(
            task.canonical_url
        )
        temp_dir = os.path.dirname(video_path)

        # Attach null-safe metrics
        metrics = ReelMetrics(
            task_id=task.id,
            views=metrics_dict.get("views"),
            likes=metrics_dict.get("likes"),
            comments=metrics_dict.get("comments"),
            author=metrics_dict.get("author"),
            upload_date=metrics_dict.get("upload_date"),
        )
        task.metrics = metrics

        # Step 3: Transition to ANALYZING
        task.status = TaskStatus.ANALYZING.value
        await session.commit()
        logger.info("Task %s: status -> ANALYZING (has_audio=%s)", task_id, has_audio)

        # Step 4: Multimodal AI analysis
        vlm = VlmClient()
        raw_obs = await vlm.analyze_video(video_path=video_path, has_audio=has_audio)

        # Step 5: Deterministic Skycoach rule engine
        evaluated_analysis = SkycoachRuleEngine.evaluate(raw_obs)
        analysis = IntegrationAnalysis(
            task_id=task.id,
            integration_class=evaluated_analysis.integration_class,
            prominence_score=evaluated_analysis.prominence_score,
            has_correct_logo=evaluated_analysis.has_correct_logo,
            banner_duration_seconds=evaluated_analysis.banner_duration_seconds,
            screen_percentage=evaluated_analysis.screen_percentage,
            has_voice_cta=evaluated_analysis.has_voice_cta,
            has_text_cta=evaluated_analysis.has_text_cta,
            promo_code=evaluated_analysis.promo_code,
            defects=evaluated_analysis.defects,
            deduction_percent=evaluated_analysis.deduction_percent,
            payout_recommendation=evaluated_analysis.payout_recommendation,
            reasoning=evaluated_analysis.reasoning,
        )
        task.analysis = analysis

        # Step 6: Mark COMPLETED
        task.status = TaskStatus.COMPLETED.value
        task.error_message = None
        await session.commit()
        logger.info(
            "Task %s: status -> COMPLETED (class=%s, score=%s, deduction=%s%%)",
            task_id,
            analysis.integration_class,
            analysis.prominence_score,
            analysis.deduction_percent,
        )
        return task

    except ExtractorError as e:
        logger.warning("Task %s failed in extractor: %s", task_id, e.user_friendly_message)
        task.status = TaskStatus.FAILED.value
        task.error_message = e.user_friendly_message
        await session.commit()
        return task

    except Exception as e:
        logger.exception("Unexpected failure processing task %s", task_id)
        task.status = TaskStatus.FAILED.value
        task.error_message = f"Внутренняя ошибка сервиса: {str(e)[:120]}"
        await session.commit()
        return task

    finally:
        # Step 7: Guaranteed video cleanup
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
                logger.debug("Cleaned up temp directory for task %s: %s", task_id, temp_dir)
            except OSError as e:
                logger.warning("Failed to clean up temp dir %s: %s", temp_dir, e)
