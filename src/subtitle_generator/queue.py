"""In-memory job queue with progress tracking and async worker."""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from subtitle_generator.config import settings
from subtitle_generator.models import JobListItem, JobStatus, TranscriptionOptions
from subtitle_generator.services.audio import AudioProcessor
from subtitle_generator.services.groq_asr import GroqASRService, TranscriptResult
from subtitle_generator.services.subtitle import Segment, SubtitleFormatter
from subtitle_generator.utils.logger import get_logger

logger = get_logger(__name__)

_ISO_639_1_MAP: dict[str, str] = {
    "english": "en",
    "chinese": "zh",
    "cantonese": "zh",   # No ISO 639-1 for Cantonese; falls under Chinese
    "japanese": "ja",
    "korean": "ko",
    "arabic": "ar",
    "french": "fr",
    "german": "de",
    "spanish": "es",
    "portuguese": "pt",
    "italian": "it",
    "russian": "ru",
    "turkish": "tr",
    "vietnamese": "vi",
    "thai": "th",
    "indonesian": "id",
    "malay": "ms",
    "hindi": "hi",
    "dutch": "nl",
    "swedish": "sv",
    "danish": "da",
    "finnish": "fi",
    "polish": "pl",
    "czech": "cs",
    "greek": "el",
    "hungarian": "hu",
    "romanian": "ro",
    "persian": "fa",
    "filipino": "tl",
    "macedonian": "mk",
}


def _to_iso639_1(language: str | None) -> str | None:
    """Convert Whisper detected language name to ISO 639-1 code."""
    if not language:
        return None
    key = language.strip().lower()
    return _ISO_639_1_MAP.get(key, key[:2] if len(key) == 2 else None)


@dataclass
class JobState:
    """Internal state of a processing job."""

    job_id: str
    media_path: Path
    output_path: Path | None = None
    status: JobStatus = JobStatus.PENDING
    progress_pct: int = 0
    stage: str = "queued"
    language: str | None = None
    error: str | None = None
    options: TranscriptionOptions = field(default_factory=TranscriptionOptions)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_list_item(self) -> JobListItem:
        """Convert to public list item."""
        return JobListItem(
            job_id=self.job_id,
            status=self.status,
            progress_pct=self.progress_pct,
            stage=self.stage,
            filename=self.media_path.name,
            language=self.language,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    def update(
        self,
        status: JobStatus | None = None,
        progress: int | None = None,
        stage: str | None = None,
    ) -> None:
        """Update job state."""
        if status is not None:
            self.status = status
        if progress is not None:
            self.progress_pct = min(100, max(0, progress))
        if stage is not None and stage != self.stage:
            self.stage = stage
            # Human-readable server log for Docker tailing
            logger.info(
                "stage_update",
                movie=self.media_path.name,
                stage=stage,
                progress=self.progress_pct,
            )
        self.updated_at = datetime.now(UTC)


class JobQueue:
    """In-memory job queue with async worker."""

    def __init__(self, asr_service: GroqASRService) -> None:
        self.asr_service = asr_service
        self._queue: asyncio.Queue[JobState] = asyncio.Queue()
        self._jobs: dict[str, JobState] = {}
        self._lock = asyncio.Lock()
        self._worker_task: asyncio.Task[None] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._shutdown = False

    async def start(self) -> None:
        """Start the worker loop and cleanup task."""
        self._worker_task = asyncio.create_task(self._worker_loop())
        self._cleanup_task = asyncio.create_task(self.cleanup_old_jobs())
        logger.info("job_queue_started")

    async def stop(self) -> None:
        """Stop the worker loop gracefully."""
        self._shutdown = True
        for task in [self._worker_task, self._cleanup_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        logger.info("job_queue_stopped")

    async def submit(
        self,
        media_path: Path,
        output_path: Path | None,
        options: TranscriptionOptions,
    ) -> JobState:
        """Submit a new job to the queue.

        Args:
            media_path: Absolute path to the media source file.
            output_path: Where the final .srt should be written. May be None
                         when language is not yet known (auto-detect).
            options: Transcription options (language, max_chars_per_line).
        """
        job_id = str(uuid.uuid4())

        job = JobState(
            job_id=job_id,
            media_path=media_path,
            output_path=output_path,
            options=options,
        )

        async with self._lock:
            self._jobs[job_id] = job

        await self._queue.put(job)
        logger.info("job_submitted", job_id=job_id, media_path=str(media_path))
        return job

    async def get(self, job_id: str) -> JobState | None:
        """Get job state by ID."""
        async with self._lock:
            job = self._jobs.get(job_id)
            if job and self._is_expired(job):
                job.status = JobStatus.EXPIRED
            return job

    async def list_jobs(self) -> list[JobState]:
        """List all non-expired jobs."""
        async with self._lock:
            now = datetime.now(UTC)
            ttl = settings.job_result_ttl_seconds
            active: list[JobState] = []
            for job in self._jobs.values():
                if (now - job.updated_at).total_seconds() > ttl:
                    job.status = JobStatus.EXPIRED
                if job.status != JobStatus.EXPIRED:
                    active.append(job)
            return active

    def _is_expired(self, job: JobState) -> bool:
        """Check if a job has expired."""
        ttl = settings.job_result_ttl_seconds
        elapsed = (datetime.now(UTC) - job.updated_at).total_seconds()
        return elapsed > ttl

    async def _worker_loop(self) -> None:
        """Main worker loop that processes jobs sequentially."""
        logger.info("worker_loop_started")
        while not self._shutdown:
            try:
                job = await self._queue.get()
                await self._process_job(job)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("worker_loop_error")
        logger.info("worker_loop_stopped")

    async def _process_job(self, job: JobState) -> None:
        """Process a single job through the Groq pipeline."""
        logger.info("processing_job", job_id=job.job_id)
        job.update(status=JobStatus.PROCESSING, stage="extracting_audio", progress=2)

        temp_dir: Path | None = None

        try:
            # Create a dedicated temp directory for this job
            temp_dir = Path(tempfile.mkdtemp(prefix=f"sg-{job.job_id}-"))
            audio = AudioProcessor()
            normalized_path = temp_dir / "normalized.mp3"

            # Stage 1: Extract + normalize audio (2-10%)
            job.update(stage="normalizing_audio", progress=5)
            await audio.normalize(job.media_path, normalized_path)

            # Stage 2: Split for Groq if too large (10-20%)
            job.update(stage="splitting_audio", progress=12)
            chunks = await audio.split_for_groq(normalized_path, temp_dir / "chunk")
            logger.info("audio_prepared", job_id=job.job_id, chunks=len(chunks))

            # Stage 3: Transcription via Groq (20-80%)
            job.update(stage="transcribing", progress=20)
            results = await self._transcribe_chunks(
                chunks,
                language=job.options.language,
                job=job,
            )

            # Stage 4: Build word-level segments and format SRT (80-100%)
            job.update(stage="building_subtitles", progress=85)
            segments = self._build_segments(chunks, results)
            formatter = SubtitleFormatter(max_chars=job.options.max_chars_per_line)
            srt_content = formatter.format(segments)

            # Determine language detected by Groq (ISO 639-1)
            detected_iso: str | None = None
            if results:
                detected_iso = _to_iso639_1(results[0].language)

            # Handle auto-detect case: compute output path now
            if job.output_path is None and detected_iso:
                job.output_path = self._srt_path(job.media_path, detected_iso)

            job.language = detected_iso

            # Write SRT next to the media file
            if job.output_path is not None:
                job.output_path.parent.mkdir(parents=True, exist_ok=True)
                job.output_path.write_text(srt_content, encoding="utf-8")
                logger.info(
                    "srt_written",
                    job_id=job.job_id,
                    path=str(job.output_path),
                )
            else:
                raise RuntimeError("Could not determine output SRT path")

            job.update(status=JobStatus.COMPLETED, stage="completed", progress=100)
            logger.info("job_completed", job_id=job.job_id)

        except Exception as exc:
            logger.error("job_failed", job_id=job.job_id, error=str(exc))
            job.update(status=JobStatus.FAILED, stage="failed")
            job.error = str(exc)

        finally:
            # Clean up temp files regardless of success/failure
            if temp_dir is not None:
                AudioProcessor.cleanup_temp_dir(temp_dir)

    async def _transcribe_chunks(
        self,
        chunks: list[tuple[Path, float]],
        language: str | None,
        job: JobState,
    ) -> list[TranscriptResult]:
        """Transcribe all chunks, updating progress."""
        total = len(chunks)
        results: list[TranscriptResult] = []

        for i, (chunk, _offset) in enumerate(chunks):
            chunk_label = f"{i + 1}/{total}"
            job.update(stage=f"transcribing chunk {chunk_label}")
            logger.info(
                "transcribing_chunk",
                job_id=job.job_id,
                chunk=chunk_label,
            )
            result = await self.asr_service.transcribe(chunk, language=language)
            results.append(result)
            progress = 20 + int(60 * (i + 1) / total)
            job.update(progress=progress)

        return results

    @staticmethod
    def _build_segments(
        chunks: list[tuple[Path, float]],
        results: list[TranscriptResult],
    ) -> list[Segment]:
        """Build unified word-level segments from chunk results with offsets."""
        segments: list[Segment] = []

        for (_chunk, offset_s), result in zip(chunks, results, strict=True):
            for word in result.words:
                segments.append(
                    Segment(
                        text=word.text,
                        start=word.start + offset_s,
                        end=word.end + offset_s,
                    )
                )

        return segments

    @staticmethod
    def _srt_path(media_path: Path, language: str) -> Path:
        """Build the SRT path next to the media file with ISO 639-1 suffix."""
        stem = AudioProcessor.strip_extension(media_path.name)
        return media_path.parent / f"{stem}.{language}.srt"

    async def cleanup_old_jobs(self) -> None:
        """Periodically clean up expired jobs from memory."""
        while not self._shutdown:
            await asyncio.sleep(60)
            async with self._lock:
                expired_ids = [
                    jid for jid, job in self._jobs.items() if job.status == JobStatus.EXPIRED
                ]
                for jid in expired_ids:
                    self._jobs.pop(jid, None)
