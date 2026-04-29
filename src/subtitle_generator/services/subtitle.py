"""SRT subtitle formatting service."""

from __future__ import annotations

from typing import TypedDict

from subtitle_generator.config import settings
from subtitle_generator.utils.logger import get_logger

logger = get_logger(__name__)


class Segment(TypedDict):
    text: str
    start: float
    end: float


class Entry(TypedDict):
    start: float
    end: float
    lines: list[str]


class SubtitleFormatter:
    """Format timestamped text into proper SRT format."""

    def __init__(self, max_chars: int = 42) -> None:
        self.max_chars = max_chars
        self.max_lines = settings.max_lines_per_subtitle
        self.min_duration = settings.min_subtitle_duration_s
        self.max_duration = settings.max_subtitle_duration_s

    def format(self, segments: list[Segment]) -> str:
        """Convert timestamped segments to SRT.

        Args:
            segments: List of segments with text, start, and end.

        Returns:
            Complete SRT file content.
        """
        if not segments:
            return ""

        # Flatten word-level segments into sentences/segments
        merged = self._merge_segments(segments)

        # Create subtitle entries
        entries = self._create_entries(merged)

        # Generate SRT
        return self._to_srt(entries)

    @staticmethod
    def _merge_segments(segments: list[Segment]) -> list[Segment]:
        """Merge very short segments and split long ones."""
        merged: list[Segment] = []

        for seg in segments:
            if not merged:
                merged.append(Segment(text=seg["text"], start=seg["start"], end=seg["end"]))
                continue

            last = merged[-1]
            combined_duration = seg["end"] - last["start"]
            combined_text = f"{last['text']} {seg['text']}"

            if (
                combined_duration <= settings.max_subtitle_duration_s
                and len(combined_text)
                <= settings.max_chars_per_line * settings.max_lines_per_subtitle
                and (seg["start"] - last["end"]) < 0.3
            ):
                last["text"] = combined_text
                last["end"] = seg["end"]
            else:
                merged.append(Segment(text=seg["text"], start=seg["start"], end=seg["end"]))

        return merged

    def _create_entries(self, segments: list[Segment]) -> list[Entry]:
        """Create subtitle entries from merged segments."""
        entries: list[Entry] = []

        for seg in segments:
            text = seg["text"].strip()
            start = seg["start"]
            end = seg["end"]
            duration = end - start

            # Enforce minimum duration
            if duration < self.min_duration:
                end = start + self.min_duration

            # Split text into lines if too long
            lines = self._split_text(text)

            entries.append(
                Entry(
                    start=start,
                    end=min(end, start + self.max_duration),
                    lines=lines,
                )
            )

        # Final pass: merge short entries that fit
        return self._merge_short_entries(entries)

    def _split_text(self, text: str) -> list[str]:
        """Split text into subtitle lines (max 2 lines, ~42 chars each)."""
        if len(text) <= self.max_chars:
            return [text]

        # Try splitting at sentence boundaries
        lines: list[str] = []
        remaining = text

        while remaining and len(lines) < self.max_lines:
            if len(remaining) <= self.max_chars:
                lines.append(remaining.strip())
                break

            # Find best split point
            split_idx = self._find_split_point(remaining)
            lines.append(remaining[:split_idx].strip())
            remaining = remaining[split_idx:].strip()

        # If still too long, truncate last line
        if len(lines) == self.max_lines and remaining:
            combined = f"{lines[-1]} {remaining}"
            if len(combined) <= self.max_chars:
                lines[-1] = combined
            else:
                lines[-1] = lines[-1][: self.max_chars - 3] + "..."

        return lines

    def _find_split_point(self, text: str) -> int:
        """Find the best position to split text (~max_chars)."""
        max_idx = min(len(text), self.max_chars)

        # Prefer splitting after punctuation
        for punct in "。！？.!?":
            idx = text.rfind(punct, 0, max_idx + 1)
            if idx > self.max_chars // 2:
                return idx + 1

        # Prefer splitting at space
        idx = text.rfind(" ", 0, max_idx + 1)
        if idx > self.max_chars // 2:
            return idx + 1

        # Fallback: hard split
        return max_idx

    @staticmethod
    def _merge_short_entries(entries: list[Entry]) -> list[Entry]:
        """Merge very short adjacent entries."""
        if not entries:
            return []

        merged: list[Entry] = [
            Entry(
                start=entries[0]["start"], end=entries[0]["end"], lines=list(entries[0]["lines"])
            )
        ]

        for entry in entries[1:]:
            last = merged[-1]
            combined_duration = entry["end"] - last["start"]
            combined_lines = last["lines"] + entry["lines"]
            combined_text = " ".join(combined_lines)

            if (
                combined_duration <= settings.max_subtitle_duration_s
                and len(combined_text)
                <= settings.max_chars_per_line * settings.max_lines_per_subtitle
                and (entry["start"] - last["end"]) < 0.5
                and len(last["lines"]) + len(entry["lines"]) <= settings.max_lines_per_subtitle
            ):
                last["lines"] = combined_lines
                last["end"] = entry["end"]
            else:
                merged.append(
                    Entry(start=entry["start"], end=entry["end"], lines=list(entry["lines"]))
                )

        return merged

    @staticmethod
    def _to_srt(entries: list[Entry]) -> str:
        """Convert entries to SRT format string."""
        lines: list[str] = []
        for i, entry in enumerate(entries, start=1):
            start = SubtitleFormatter._format_time(entry["start"])
            end = SubtitleFormatter._format_time(entry["end"])
            text = "\n".join(entry["lines"])
            lines.append(f"{i}\n{start} --> {end}\n{text}\n")

        return "\n".join(lines)

    @staticmethod
    def _format_time(seconds: float) -> str:
        """Format seconds as SRT timestamp (HH:MM:SS,mmm)."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
