"""SRT subtitle formatting service."""

from __future__ import annotations

from typing import TypedDict

from subtitle_generator.config import settings
from subtitle_generator.utils.logger import get_logger

logger = get_logger(__name__)

# Soft limits — we'll try to break at natural points near these
_SOFT_CHARS_PER_LINE = 42
_SOFT_CHARS_MAX = 50  # Hard ceiling to avoid ridiculously long lines


class Segment(TypedDict):
    text: str
    start: float
    end: float


class Entry(TypedDict):
    start: float
    end: float
    lines: list[str]


class SubtitleFormatter:
    """Format timestamped text into proper SRT format.

    Key principles:
    - Never discard words — overflow creates new subtitle entries
    - Word-level splitting with soft character limits
    - Natural break points: punctuation > prepositions > word boundaries
    - Max 2 lines per subtitle entry (hard limit)
    """

    def __init__(self, max_chars: int = _SOFT_CHARS_PER_LINE) -> None:
        self.target_chars = max_chars
        self.max_chars = _SOFT_CHARS_MAX
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

        # Create subtitle entries (may create multiple per segment if long)
        entries = self._create_entries(merged)

        # Generate SRT
        return self._to_srt(entries)

    @staticmethod
    def _merge_segments(segments: list[Segment]) -> list[Segment]:
        """Merge very short segments and split long ones."""
        merged: list[Segment] = []

        for seg in segments:
            if not merged:
                merged.append(
                    Segment(text=seg["text"], start=seg["start"], end=seg["end"])
                )
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
                merged.append(
                    Segment(text=seg["text"], start=seg["start"], end=seg["end"])
                )

        return merged

    def _create_entries(self, segments: list[Segment]) -> list[Entry]:
        """Create subtitle entries from merged segments.

        Creates multiple entries when text overflows 2 lines.
        Never discards words.
        """
        entries: list[Entry] = []

        for seg in segments:
            text = seg["text"].strip()
            if not text:
                continue

            words = text.split()
            seg_start = seg["start"]
            seg_end = seg["end"]
            seg_duration = max(seg_end - seg_start, self.min_duration)

            # Split into one or more entries (never truncate)
            entry_words_list = self._split_into_entry_words(words)

            # Distribute time proportionally among entries
            total_words = len(words)
            current_start = seg_start

            for entry_words in entry_words_list:
                entry_word_count = len(entry_words)
                # Proportional duration based on word count
                entry_duration = seg_duration * (entry_word_count / total_words)
                entry_end = min(current_start + entry_duration, seg_end)

                # Ensure minimum duration
                if entry_end - current_start < self.min_duration:
                    entry_end = current_start + self.min_duration

                lines = self._words_to_lines(entry_words)

                entries.append(
                    Entry(
                        start=current_start,
                        end=min(entry_end, current_start + self.max_duration),
                        lines=lines,
                    )
                )

                # Small gap between entries
                current_start = entry_end + 0.05

        # Final pass: merge short adjacent entries that fit together
        return self._merge_short_entries(entries)

    def _split_into_entry_words(self, words: list[str]) -> list[list[str]]:
        """Split words into groups, each fitting in max_lines lines.

        Never discards words — creates as many groups as needed.
        Each line must also respect max_chars limit.
        """
        if not words:
            return []

        groups: list[list[str]] = []
        current_group: list[str] = []

        for word in words:
            test_group = current_group + [word]
            lines = self._words_to_lines(test_group)

            # Check both line count AND individual line lengths
            lines_valid = len(lines) <= self.max_lines and all(
                len(line) <= self.max_chars for line in lines
            )

            if lines_valid:
                # Fits — add to current group
                current_group = test_group
            else:
                # Would exceed limits — start new group
                if current_group:
                    groups.append(current_group)
                current_group = [word]

        # Don't forget the last group
        if current_group:
            groups.append(current_group)

        return groups

    def _words_to_lines(self, words: list[str]) -> list[str]:
        """Convert words into 1-2 lines with soft character limits.

        Tries to break at natural points near target_chars.
        Respects max_chars as hard ceiling.
        """
        if not words:
            return []

        text = " ".join(words)

        # Short enough for single line
        if len(text) <= self.target_chars:
            return [text]

        # Try to split into 2 lines
        lines = self._split_at_natural_point(words, self.target_chars)

        # If second line is still too long, we need more entries
        # (handled by caller checking len(lines))
        return lines

    def _split_at_natural_point(
        self, words: list[str], target_chars: int
    ) -> list[str]:
        """Split words at a natural break point near target_chars."""
        if not words:
            return []

        # Build text to find split point
        text = " ".join(words)

        if len(text) <= target_chars:
            return [text]

        # Find best split point near target_chars
        # Look at accumulated word lengths
        char_count = 0
        best_split = 0
        hard_limit = min(self.max_chars, len(text) // 2 + 10)

        for i, word in enumerate(words):
            # +1 for space (except first word)
            add_len = len(word) if i == 0 else len(word) + 1

            if char_count + add_len > target_chars:
                # We've exceeded target — check if this is a good split point
                if char_count > 0:
                    best_split = i
                break

            char_count += add_len

            # Prefer punctuation boundaries
            if char_count >= target_chars * 0.7:  # Within 70% of target
                # Check if this word ends with punctuation
                if any(word.endswith(p) for p in ".!,;:?"):
                    best_split = i + 1
                    break

        if best_split == 0 or best_split >= len(words):
            # Fallback: split around middle, respecting hard limit
            mid = len(words) // 2
            for i in range(len(words)):
                test_text = " ".join(words[:i])
                if len(test_text) > hard_limit and i > 0:
                    best_split = i
                    break
            else:
                best_split = max(1, mid)

        first_line = " ".join(words[:best_split]).strip()
        second_line = " ".join(words[best_split:]).strip()

        lines = [first_line] if first_line else []
        if second_line:
            lines.append(second_line)

        return lines

    def _merge_short_entries(self, entries: list[Entry]) -> list[Entry]:
        """Merge very short adjacent entries that fit together."""
        if not entries:
            return []

        merged: list[Entry] = [
            Entry(
                start=entries[0]["start"],
                end=entries[0]["end"],
                lines=list(entries[0]["lines"]),
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
                and len(last["lines"]) + len(entry["lines"])
                <= settings.max_lines_per_subtitle
            ):
                last["lines"] = combined_lines
                last["end"] = entry["end"]
            else:
                merged.append(
                    Entry(
                        start=entry["start"],
                        end=entry["end"],
                        lines=list(entry["lines"]),
                    )
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
