import base64
import glob
import json
import logging
import os
import shutil
import subprocess
import tempfile

from openai import AsyncOpenAI

from src.config import settings
from src.schemas.analysis import VlmRawObservation
from src.services.ai.prompts import (
    BANNER_MEASURE_PROMPT,
    SKYCOACH_SYSTEM_PROMPT,
    SKYCOACH_USER_PROMPT,
)

logger = logging.getLogger(__name__)


class VlmClient:
    """
    Universal OpenAI-Compatible Multimodal VLM client.
    Works seamlessly with:
    - OpenRouter (qwen/qwen3.8-omni-flash, gpt-4o-mini, etc.)
    - Alibaba DashScope (https://dashscope.aliyuncs.com/compatible-mode/v1)
    - SiliconFlow (https://api.siliconflow.cn/v1)
    - Local vLLM / Ollama
    - OpenAI / Google Gemini (as secondary fallback)
    """

    def __init__(self):
        self.api_key = settings.effective_api_key
        self.base_url = settings.effective_base_url
        self.model = settings.effective_model
        self.provider = settings.AI_PROVIDER

    async def analyze_video(
        self,
        video_path: str,
        has_audio: bool = True,
        override_observation: VlmRawObservation | None = None,
    ) -> VlmRawObservation:
        """
        Runs multimodal video analysis and returns structured VlmRawObservation.
        """
        if override_observation:
            return override_observation

        # If video file does not exist on disk, fall back to heuristic
        if not os.path.exists(video_path):
            logger.warning(
                "Video file %s not found on disk, falling back to heuristic observation.",
                video_path,
            )
            return self._generate_heuristic_observation(video_path, has_audio)

        # 1. Primary: Any OpenAI-compatible multimodal endpoint
        if self.api_key and self.provider != "gemini":
            obs = await self._analyze_with_openai_compatible(video_path, has_audio)
            if obs.has_skycoach_mention:
                obs = await self._refine_with_banner_measurement(obs, video_path)
            return obs

        # 2. Secondary: Google Gemini (if explicitly chosen)
        if settings.GEMINI_API_KEY and self.provider == "gemini":
            return await self._analyze_with_gemini(video_path, has_audio)

        # 3. Fallback / Mock mode when no API keys are configured (for local dev / CI tests)
        logger.warning(
            "No active AI API keys configured. Using deterministic heuristic mock observation."
        )
        return self._generate_heuristic_observation(video_path, has_audio)

    async def _analyze_with_openai_compatible(
        self, video_path: str, has_audio: bool
    ) -> VlmRawObservation:
        # Close the HTTP client before the event loop ends: each queued task runs
        # in its own asyncio.run(), and a leaked client errors on loop shutdown.
        async with AsyncOpenAI(base_url=self.base_url, api_key=self.api_key) as client:
            return await self._run_openai_analysis(client, video_path, has_audio)

    async def _run_openai_analysis(
        self, client: AsyncOpenAI, video_path: str, has_audio: bool
    ) -> VlmRawObservation:
        """
        Universal OpenAI-compatible multimodal analysis.
        Attempts direct video stream; if the endpoint/tier requires frames or returns
        a video balance constraint (e.g. OpenRouter 402 for direct video), automatically
        extracts high-resolution keyframes via ffmpeg and sends them as vision image_url items.
        """

        prompt_text = SKYCOACH_USER_PROMPT
        if not has_audio:
            prompt_text += "\nВНИМАНИЕ: Видеоролик без звуковой дорожки (has_voice_cta: false)."

        ref_logo_b64 = self._get_reference_logo_b64()

        # Try direct video_url first
        try:
            import anyio

            async with await anyio.open_file(video_path, "rb") as f:
                video_bytes = await f.read()
            b64_video = base64.b64encode(video_bytes).decode("utf-8")

            direct_content: list[dict] = []
            if ref_logo_b64:
                direct_content.extend(
                    [
                        {
                            "type": "text",
                            "text": (
                                "ЭТАЛОН ОФИЦИАЛЬНОГО ЛОГОТИПА SKYCOACH (REFERENCE LOGO):\n"
                                "Ниже приведено официальное изображение бренда Skycoach. "
                                "Сверяй баннеры и логотипы в видеоролике с этим эталоном:\n"
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{ref_logo_b64}",
                                "detail": "high",
                            },
                        },
                    ]
                )
            direct_content.extend(
                [
                    {"type": "text", "text": prompt_text},
                    {
                        "type": "video_url",
                        "video_url": {"url": f"data:video/mp4;base64,{b64_video}"},
                    },
                ]
            )

            logger.info("Sending direct video stream to OpenAI-compatible API (%s)...", self.model)
            response = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SKYCOACH_SYSTEM_PROMPT},
                    {"role": "user", "content": direct_content},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
            )
            return self._parse_observation(response.choices[0].message.content)

        except Exception as e:  # noqa: BLE001
            err_msg = str(e)
            logger.info(
                "Direct video_url unsupported or tier constrained (%s). "
                "Switching to universal keyframe vision mode...",
                err_msg[:120],
            )

        # Universal Keyframe Vision Mode (works across ALL OpenAI-compatible multimodal providers)
        keyframes_b64 = self._extract_keyframes(video_path, max_frames=6)
        user_content: list[dict] = []

        if ref_logo_b64:
            user_content.extend(
                [
                    {
                        "type": "text",
                        "text": (
                            "ЭТАЛОН ОФИЦИАЛЬНОГО ЛОГОТИПА SKYCOACH (REFERENCE LOGO ДЛЯ СВЕРКИ):\n"
                            "Ниже представлено официальное изображение логотипа бренда Skycoach. "
                            "Внимательно сравнивай любые баннеры, оверлеи и логотипы в видеоролике с этим эталоном.\n"
                            "Если на баннере отображается чужой логотип, логотип конкурента или логотип не соответствует бренду Skycoach — "
                            "ты ОБЯЗАН установить: has_correct_logo: false и добавить дефект 'wrong_logo'."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{ref_logo_b64}",
                            "detail": "high",
                        },
                    },
                    {
                        "type": "text",
                        "text": f"КАДРЫ АНАЛИЗИРУЕМОГО ВИДЕОРОЛИКА:\n{prompt_text}",
                    },
                ]
            )
        else:
            user_content.append({"type": "text", "text": prompt_text})

        for idx, b64_img in enumerate(keyframes_b64):
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{b64_img}",
                        "detail": "high",
                    },
                }
            )

        logger.info(
            "Sending reference logo + %d keyframes to OpenAI-compatible endpoint (%s at %s)...",
            len(keyframes_b64),
            self.model,
            self.base_url,
        )

        # The model occasionally answers with an empty object; one retry fixes it
        last_error: Exception | None = None
        for attempt in range(1, 3):
            response = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SKYCOACH_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
            )
            raw_json = response.choices[0].message.content
            logger.debug("Raw OpenAI-compatible response: %s", raw_json)
            try:
                return self._parse_observation(raw_json)
            except ValueError as e:
                last_error = e
                logger.warning("Unusable VLM answer (attempt %d): %s", attempt, str(e)[:200])
        raise RuntimeError(f"Модель вернула некорректный ответ: {str(last_error)[:100]}")

    @staticmethod
    def _parse_observation(raw_json: str | None) -> VlmRawObservation:
        """
        Parses a VLM answer into an observation. Raises ValueError (pydantic's
        ValidationError included) when the answer is empty or malformed.
        """
        parsed = json.loads(raw_json or "{}")

        # Handle models returning a list of frame observations
        if isinstance(parsed, list):
            # Prioritize frame item with detected ad, or fall back to first item
            ad_item = next(
                (
                    item
                    for item in parsed
                    if isinstance(item, dict) and item.get("has_skycoach_mention")
                ),
                None,
            )
            parsed = (
                ad_item
                if ad_item
                else (parsed[0] if parsed and isinstance(parsed[0], dict) else {})
            )

        # Handle nested wrappers {"observation": {...}}
        if isinstance(parsed, dict):
            if "observation" in parsed and isinstance(parsed["observation"], dict):
                parsed = parsed["observation"]
            elif "result" in parsed and isinstance(parsed["result"], dict):
                parsed = parsed["result"]

        return VlmRawObservation(**parsed)

    async def _refine_with_banner_measurement(
        self, obs: VlmRawObservation, video_path: str
    ) -> VlmRawObservation:
        """
        Second pass: the VLM's own area/duration guesses from a whole video are
        noisy (a 2.4% banner came back as 4.5-6.5%), but it localizes a banner
        on individual frames precisely. Ask for per-frame boxes and derive
        area, on-screen duration and promo code from them.
        """
        try:
            measurement = await self._measure_banner(video_path)
        except Exception as e:  # noqa: BLE001
            logger.warning("Banner measurement failed, keeping VLM estimates: %s", e)
            return obs
        if not measurement:
            return obs

        bbox, duration, promo_code = measurement
        update = obs.model_dump()
        update["banner_bbox"] = bbox
        update["banner_duration_seconds"] = duration
        if promo_code:
            update["promo_code"] = promo_code
        # Size is now measured, so the rule engine decides "too_small" by itself
        update["observed_defects"] = [d for d in obs.observed_defects if d != "too_small"]
        refined = VlmRawObservation(**update)
        logger.info(
            "Banner measured: bbox=%s area=%.2f%% duration=%.1fs promo=%s",
            bbox,
            refined.screen_percentage,
            duration,
            refined.promo_code,
        )
        return refined

    async def _measure_banner(
        self, video_path: str, num_frames: int = 12
    ) -> tuple[list[float], float, str | None] | None:
        """
        Returns (largest banner bbox in 0-1000 coords, seconds on screen, promo code),
        or None if no banner was found on any sampled frame.
        """
        duration = self._probe_duration(video_path)
        if not duration:
            return None
        timestamps = [duration * (i + 0.5) / num_frames for i in range(num_frames)]
        frames = self._extract_frames_at(video_path, timestamps)
        if not frames:
            return None

        content: list[dict] = []
        for idx, (ts, b64_img) in enumerate(zip(timestamps, frames, strict=True)):
            content.append({"type": "text", "text": f"Кадр {idx} (t={ts:.1f}s):"})
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64_img}", "detail": "high"},
                }
            )
        content.append({"type": "text", "text": BANNER_MEASURE_PROMPT})

        async with AsyncOpenAI(
            base_url=self.base_url, api_key=self.api_key, max_retries=4
        ) as client:
            response = await client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": content}],
                response_format={"type": "json_object"},
                temperature=0,
            )
        parsed = json.loads(response.choices[0].message.content or "{}")
        # Some answers skip the wrapper and return the per-frame list directly
        if isinstance(parsed, list):
            parsed = {"frames": parsed}
        if not isinstance(parsed, dict):
            return None

        boxes: list[list[float]] = []
        for item in parsed.get("frames") or []:
            bbox = item.get("bbox") if isinstance(item, dict) else None
            if (
                isinstance(bbox, list)
                and len(bbox) == 4
                and all(isinstance(v, (int, float)) for v in bbox)
                and bbox[2] > bbox[0]
                and bbox[3] > bbox[1]
            ):
                boxes.append([float(v) for v in bbox])
        if not boxes:
            return None

        largest = max(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
        seconds_on_screen = round(len(boxes) * duration / num_frames, 1)
        promo_code = parsed.get("promo_code") or None
        return largest, seconds_on_screen, promo_code

    @staticmethod
    def _probe_duration(video_path: str) -> float | None:
        try:
            out = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "csv=p=0",
                    video_path,
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            return float(out.strip())
        except (OSError, subprocess.CalledProcessError, ValueError):
            return None

    @staticmethod
    def _extract_frames_at(video_path: str, timestamps: list[float]) -> list[str]:
        """Extracts one 720px-wide JPEG per timestamp and returns them base64-encoded."""
        temp_dir = tempfile.mkdtemp(prefix="banner_frames_")
        try:
            b64_frames = []
            for idx, ts in enumerate(timestamps):
                out_path = os.path.join(temp_dir, f"frame_{idx:03d}.jpg")
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    f"{ts:.2f}",
                    "-i",
                    video_path,
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=720:-2",
                    "-q:v",
                    "3",
                    out_path,
                ]
                subprocess.run(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
                )
                with open(out_path, "rb") as f:
                    b64_frames.append(base64.b64encode(f.read()).decode("utf-8"))
            return b64_frames
        except (OSError, subprocess.CalledProcessError) as e:
            logger.warning("Could not extract frames for banner measurement: %s", e)
            return []
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @classmethod
    def _get_reference_logo_b64(cls) -> str | None:
        """
        Loads the official Skycoach reference logo image as base64 JPEG
        for visual few-shot grounding / logo comparison.
        """
        ref_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "ui",
            "static",
            "skycoach_reference_logo.jpg",
        )
        if os.path.exists(ref_path):
            try:
                with open(ref_path, "rb") as f:
                    return base64.b64encode(f.read()).decode("utf-8")
            except (OSError, ValueError) as e:
                logger.warning("Could not read reference logo: %s", e)
        return None

    @staticmethod
    def _extract_keyframes(video_path: str, max_frames: int = 6) -> list[str]:
        """
        Extracts up to `max_frames` evenly spaced JPEG frames from video using ffmpeg
        and encodes them to base64 strings.
        """
        temp_dir = tempfile.mkdtemp(prefix="keyframes_")
        out_pattern = os.path.join(temp_dir, "frame_%03d.jpg")

        try:
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                video_path,
                "-vf",
                "fps=1,scale=720:-1",
                "-q:v",
                "3",
                out_pattern,
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

            all_frames = sorted(glob.glob(os.path.join(temp_dir, "*.jpg")))
            if not all_frames:
                return []

            if len(all_frames) > max_frames:
                step = len(all_frames) / max_frames
                selected = [all_frames[int(i * step)] for i in range(max_frames)]
            else:
                selected = all_frames

            b64_frames = []
            for f_path in selected:
                with open(f_path, "rb") as f:
                    b64_frames.append(base64.b64encode(f.read()).decode("utf-8"))

            return b64_frames

        finally:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

    async def _analyze_with_gemini(self, video_path: str, has_audio: bool) -> VlmRawObservation:
        """Invokes Google Gemini 2.5 Flash via google-genai SDK."""
        import asyncio

        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        logger.info("Uploading video to Gemini Files API: %s", video_path)
        video_file = client.files.upload(file=video_path)

        while video_file.state == types.FileState.PROCESSING:
            await asyncio.sleep(2)
            video_file = client.files.get(name=video_file.name)

        if video_file.state == types.FileState.FAILED:
            raise RuntimeError(f"Gemini video processing failed: {video_file.error}")

        prompt_text = SKYCOACH_USER_PROMPT
        if not has_audio:
            prompt_text += "\nВНИМАНИЕ: Видеоролик без звуковой дорожки, голосовой CTA отсутствует."

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[video_file, prompt_text],
            config=types.GenerateContentConfig(
                system_instruction=SKYCOACH_SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=VlmRawObservation,
                temperature=0.1,
            ),
        )

        try:
            client.files.delete(name=video_file.name)
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed to delete Gemini temporary file %s: %s", video_file.name, e)

        parsed_data = json.loads(response.text)
        return VlmRawObservation(**parsed_data)

    def _generate_heuristic_observation(
        self, video_path: str, has_audio: bool
    ) -> VlmRawObservation:
        """Safe deterministic fallback when running locally without API keys."""
        file_size_mb = (
            os.path.getsize(video_path) / (1024 * 1024) if os.path.exists(video_path) else 1.0
        )

        return VlmRawObservation(
            has_skycoach_mention=True,
            is_product_advertised=True,
            banner_duration_seconds=min(12.0, max(5.0, file_size_mb * 2.5)),
            screen_percentage=11.5,
            has_voice_cta=has_audio,
            has_text_cta=True,
            promo_code="SKYCOACH",
            observed_defects=[],
            visual_observations=(
                "Баннер Skycoach размещен по центру кадра, логотип отчетливо виден. "
                "Рекламируются услуги бустинга и промокод на скидку."
            ),
        )
