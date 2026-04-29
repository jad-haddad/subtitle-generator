"""Audio processing via FFmpeg for Groq Whisper API."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

from subtitle_generator.config import settings
from subtitle_generator.utils.logger import get_logger

logger = get_logger(__name__)

# Supported audio/video input extensions
SUPPORTED_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".m4a",
    ".mp4",
    ".mkv",
    ".flac",
    ".ogg",
    ".aac",
    ".wma",
    ".webm",
    ".mov",
    ".avi",
    ".wmv",
}


class AudioFormatError(Exception):
    """Raised when audio format is unsupported."""


class CorruptedFileError(Exception):
    """Raised when audio file is corrupted."""


class AudioProcessor:
    """Process audio files using FFmpeg."""

    @staticmethod
    def validate_extension(filename: str) -> None:
        """Validate file extension is supported."""
        ext = Path(filename).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            msg = (
                f"Unsupported file format: {ext}. "
                f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            )
            raise AudioFormatError(msg)

    @staticmethod
    def strip_extension(filename: str) -> str:
        """Remove known audio/video extension from filename."""
        path = Path(filename)
        if path.suffix.lower() in SUPPORTED_EXTENSIONS:
            return path.stem
        return filename

    @staticmethod
    def validate_with_ffprobe(input_path: Path) -> None:
        """Validate file integrity with ffprobe."""
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(input_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if result.returncode != 0:
                raise CorruptedFileError(
                    f"File validation failed: {result.stderr.strip()}"
                )
            duration = result.stdout.strip()
            if not duration or float(duration) <= 0:
                raise CorruptedFileError("File has no valid audio duration")
        except subprocess.TimeoutExpired:
            raise CorruptedFileError("File validation timed out") from None
        except ValueError as exc:
            raise CorruptedFileError(f"Invalid file metadata: {exc}") from exc

    async def normalize(self, input_path: Path, output_path: Path) -> Path:
        """Normalize audio to MP3 32kbps mono for Groq Whisper.

        Args:
            input_path: Path to input audio/video file.
            output_path: Path where normalized MP3 will be written.

        Returns:
            Path to normalized MP3 file.
        """
        logger.info("normalizing_audio", input=str(input_path), output=str(output_path))
        self.validate_with_ffprobe(input_path)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-ar",
            "16000",
            "-ac",
            "1",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            settings.mp3_bitrate,
            "-vn",
            "-hide_banner",
            "-loglevel",
            "error",
            str(output_path),
        ]
        await self._run_ffmpeg(cmd)
        logger.info("audio_normalized", output=str(output_path))
        return output_path

    async def split_for_groq(
        self,
        audio_path: Path,
        output_prefix: Path,
    ) -> list[tuple[Path, float]]:
        """Split long audio into Groq-safe chunks (under safe chunk size).

        Uses a safety margin below Groq's hard 25MB limit to avoid
        413 Request Entity Too Large errors.

        Args:
            audio_path: Path to normalized MP3 audio file.
            output_prefix: Path prefix for chunks (e.g. /tmp/job/chunk).

        Returns:
            List of (chunk_path, offset_seconds) tuples.
        """
        audio_path = Path(audio_path)
        size_mb = audio_path.stat().st_size / (1024 * 1024)
        safe_mb = settings.groq_safe_chunk_size_mb

        if size_mb <= safe_mb:
            logger.info("audio_fits_groq", size_mb=f"{size_mb:.1f}", safe_mb=safe_mb)
            return [(audio_path, 0.0)]

        total_duration = self._get_duration(audio_path)

        # Estimate chunk duration: if the whole file is `size_mb` MB,
        # then a `safe_mb` chunk is roughly `safe_mb / size_mb * total_duration`
        # seconds. We round down to be conservative.
        chunk_duration_s = max(30, int((safe_mb / size_mb) * total_duration * 0.9))
        chunk_duration_s = min(chunk_duration_s, settings.chunk_duration_s)

        logger.info(
            "splitting_audio",
            total_mb=f"{size_mb:.1f}",
            safe_mb=safe_mb,
            chunk_duration=chunk_duration_s,
        )

        output_dir = output_prefix.parent
        output_stem = output_prefix.name
        output_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(audio_path),
            "-f",
            "segment",
            "-segment_time",
            str(chunk_duration_s),
            "-ar",
            "16000",
            "-ac",
            "1",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            settings.mp3_bitrate,
            str(output_dir / f"{output_stem}_%03d.mp3"),
        ]
        await self._run_ffmpeg(cmd)
        chunks = sorted(output_dir.glob(f"{output_stem}_*.mp3"))
        logger.info("audio_split", chunks=len(chunks))

        # Safety check: verify no chunk exceeds the safe limit
        violations = [
            (chunk, chunk.stat().st_size / (1024 * 1024))
            for chunk in chunks
            if chunk.stat().st_size / (1024 * 1024) > safe_mb
        ]
        if violations:
            for chunk, chunk_mb in violations:
                logger.warning(
                    "chunk_oversized",
                    chunk=str(chunk),
                    chunk_mb=f"{chunk_mb:.1f}",
                    safe_mb=safe_mb,
                )

        # Estimate offset for each chunk based on index and chunk_duration_s
        return [
            (chunk, float(idx * chunk_duration_s))
            for idx, chunk in enumerate(chunks)
        ]

    @staticmethod
    def _get_duration(audio_path: Path) -> float:
        """Get audio duration in seconds via ffprobe."""
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return float(result.stdout.strip() or 0)

    @staticmethod
    def _get_chunk_offsets(audio_path: Path) -> list[float]:  # noqa: ARG004
        """Placeholder for parsing actual chunk segment boundaries.

        The `audio_path` parameter is reserved for future ffprobe-based
        segment boundary extraction.
        """
        return [0.0]

    async def _run_ffmpeg(self, cmd: list[str]) -> None:
        """Run FFmpeg command asynchronously."""
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            ),
        )
        if result.returncode != 0:
            raise CorruptedFileError(f"FFmpeg failed: {result.stderr.strip()}")

    @staticmethod
    def cleanup_temp_dir(temp_dir: Path) -> None:
        """Recursively remove a temporary directory."""
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.info("temp_dir_cleaned", path=str(temp_dir))
