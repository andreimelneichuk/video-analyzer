import base64
import json
import logging
import os

from openai import AsyncOpenAI

from src.config import settings
from src.schemas.analysis import VlmRawObservation
from src.services.ai.prompts import SKYCOACH_SYSTEM_PROMPT, SKYCOACH_USER_PROMPT

logger = logging.getLogger(__name__)


class VlmClient:
    """
    Multimodal VLM client supporting Gemini 2.5 Flash (via Google GenAI)
    and OpenRouter (Qwen Omni / GPT-4o-mini via OpenAI SDK).
    Includes offline mock capability for test environments and CI.
    """

    def __init__(self):
        self.provider = settings.AI_PROVIDER
        self.gemini_key = settings.GEMINI_API_KEY
        self.openrouter_key = settings.OPENROUTER_API_KEY

    async def analyze_video(
        self,
        video_path: str,
        has_audio: bool = True,
        override_observation: VlmRawObservation | None = None,
    ) -> VlmRawObservation:
        """
        Runs multimodal video analysis and returns structured VlmRawObservation.
        """
        # If explicit override provided (e.g. for unit tests)
        if override_observation:
            return override_observation

        # 1. Check for Gemini
        if self.provider == "gemini" and self.gemini_key:
            return await self._analyze_with_gemini(video_path, has_audio)

        # 2. Check for OpenRouter / Qwen Omni
        if (self.provider == "openrouter" or self.openrouter_key) and self.openrouter_key:
            return await self._analyze_with_openrouter(video_path, has_audio)

        # 3. Fallback / Mock mode when no API keys are configured (for local dev / CI tests)
        logger.warning(
            "No active AI API keys configured (GEMINI_API_KEY / OPENROUTER_API_KEY). "
            "Using deterministic heuristic mock observation."
        )
        return self._generate_heuristic_observation(video_path, has_audio)

    async def _analyze_with_gemini(self, video_path: str, has_audio: bool) -> VlmRawObservation:
        """Invokes Google Gemini 2.5 Flash via google-genai SDK."""
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.gemini_key)

        # Upload video to Gemini Files API
        logger.info("Uploading video to Gemini Files API: %s", video_path)
        video_file = client.files.upload(file=video_path)

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

        # Cleanup file in Gemini storage
        try:
            client.files.delete(name=video_file.name)
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed to delete Gemini temporary file %s: %s", video_file.name, e)

        parsed_data = json.loads(response.text)
        return VlmRawObservation(**parsed_data)

    async def _analyze_with_openrouter(self, video_path: str, has_audio: bool) -> VlmRawObservation:
        """Invokes Qwen Omni / GPT-4o-mini via OpenRouter."""
        import anyio

        client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=self.openrouter_key,
        )

        async with await anyio.open_file(video_path, "rb") as f:
            video_bytes = await f.read()
        b64_video = base64.b64encode(video_bytes).decode("utf-8")

        prompt_text = SKYCOACH_USER_PROMPT
        if not has_audio:
            prompt_text += "\nВНИМАНИЕ: Видеоролик без звука (has_voice_cta: false)."

        response = await client.chat.completions.create(
            model="qwen/qwen-2.5-omni",
            messages=[
                {"role": "system", "content": SKYCOACH_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {
                            "type": "video_url",
                            "video_url": {"url": f"data:video/mp4;base64,{b64_video}"},
                        },
                    ],
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )

        raw_json = response.choices[0].message.content
        parsed = json.loads(raw_json)
        return VlmRawObservation(**parsed)

    def _generate_heuristic_observation(
        self, video_path: str, has_audio: bool
    ) -> VlmRawObservation:
        """
        Safe deterministic fallback when running locally without API keys.
        Detects video size and duration heuristics for previewing.
        """
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
