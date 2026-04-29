"""Groq Whisper API service for transcription."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from groq import AsyncGroq
from groq.types.audio.transcription_create_params import FileTypes

from subtitle_generator.config import settings
from subtitle_generator.utils.logger import get_logger

logger = get_logger(__name__)

# Groq documents 25MB but in practice the CDN often rejects anything
# above ~20MB. We enforce a hard runtime ceiling to get a clean error
# message instead of an opaque 413.
_HARD_UPLOAD_CEILING_MB = 24.0


@dataclass
class TranscriptWord:
    """A single word with precise timestamp."""

    text: str
    start: float
    end: float


@dataclass
class TranscriptResult:
    """Result from a single transcription."""

    text: str
    words: list[TranscriptWord]
    language: str | None = None


class GroqASRService:
    """Groq Whisper API transcription service."""

    def __init__(self) -> None:
        self.client = AsyncGroq(
            api_key=settings.groq_api_key,
            base_url=settings.groq_base_url,
        )
        self.concurrency_sem = asyncio.Semaphore(settings.groq_concurrency)

    def load(self) -> None:
        """No-op: Groq client is lightweight, no heavy model loading."""
        logger.info("groq_asr_ready", model=settings.groq_model)

    async def transcribe(self, audio_path: Path, language: str | None = None) -> TranscriptResult:
        """Transcribe a single audio file via Groq."""
        async with self.concurrency_sem:
            return await self._transcribe_single(str(audio_path), language)

    async def transcribe_batch(
        self,
        audio_paths: list[Path],
        language: str | None = None,
    ) -> list[TranscriptResult]:
        """Transcribe multiple files concurrently, respecting rate limits."""
        tasks = [
            self.transcribe(path, language=language)
            for path in audio_paths
        ]
        return await asyncio.gather(*tasks)

    async def _transcribe_single(
        self,
        audio_path: str,
        language: str | None = None,
    ) -> TranscriptResult:
        """Synchronous transcription via Groq."""
        audio_path_obj = Path(audio_path)
        size_bytes = audio_path_obj.stat().st_size
        size_mb = size_bytes / (1024 * 1024)

        logger.info(
            "groq_upload_start",
            file=audio_path,
            size_mb=f"{size_mb:.2f}",
            size_bytes=size_bytes,
        )

        if size_mb > _HARD_UPLOAD_CEILING_MB:
            msg = (
                f"Audio chunk too large for Groq: {size_mb:.2f}MB "
                f"(limit ~{_HARD_UPLOAD_CEILING_MB}MB). "
                f"Chunk splitting may need adjustment."
            )
            logger.error("groq_upload_oversized", file=audio_path, size_mb=f"{size_mb:.2f}")
            raise RuntimeError(msg)

        file_arg: FileTypes = ("audio.mp3", audio_path_obj.read_bytes())
        kwargs: dict[str, Any] = {
            "file": file_arg,
            "model": settings.groq_model,
            "response_format": "verbose_json",
            "timestamp_granularities": ["word"],
        }
        if language:
            kwargs["language"] = language

        response = await self.client.audio.transcriptions.create(**kwargs)

        text = response.text.strip() if response.text else ""
        words = self._parse_words(response)
        detected_language = getattr(response, "language", None)

        logger.info(
            "groq_upload_done",
            file=audio_path,
            detected_language=detected_language,
            word_count=len(words),
        )

        return TranscriptResult(
            text=text,
            words=words,
            language=detected_language,
        )

    @staticmethod
    def _parse_words(response: Any) -> list[TranscriptWord]:
        """Extract word-level timestamps from Groq verbose_json response."""
        words: list[TranscriptWord] = []

        # The response has a 'words' attribute in verbose_json mode
        raw_words: list[dict[str, Any]] = getattr(response, "words", [])
        for item in raw_words:
            words.append(
                TranscriptWord(
                    text=str(item.get("word", "")),
                    start=float(item.get("start", 0)),
                    end=float(item.get("end", 0)),
                )
            )

        # If groq returns segments with sub-word items as fallback
        if not words:
            for seg in getattr(response, "segments", []):
                for item in seg.get("words", []):
                    words.append(
                        TranscriptWord(
                            text=str(item.get("word", "")),
                            start=float(item.get("start", 0)),
                            end=float(item.get("end", 0)),
                        )
                    )

        return words
