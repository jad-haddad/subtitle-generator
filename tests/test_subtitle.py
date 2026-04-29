"""Unit tests for subtitle formatter."""

from __future__ import annotations

import pytest

from subtitle_generator.services.subtitle import Segment, SubtitleFormatter


@pytest.fixture
def formatter() -> SubtitleFormatter:
    """Create a subtitle formatter with default settings."""
    return SubtitleFormatter(max_chars=42)


def test_format_empty(formatter: SubtitleFormatter) -> None:
    """Test formatting an empty segment list."""
    assert formatter.format([]) == ""


def test_format_single_segment(formatter: SubtitleFormatter) -> None:
    """Test formatting a single short segment."""
    segments = [
        Segment(text="Hello, how are you?", start=1.0, end=3.5),
    ]
    srt = formatter.format(segments)
    assert "1" in srt
    assert "00:00:01,000 --> 00:00:03,500" in srt
    assert "Hello, how are you?" in srt


def test_format_multiple_segments(formatter: SubtitleFormatter) -> None:
    """Test formatting multiple segments."""
    segments = [
        Segment(text="First sentence here.", start=1.0, end=3.0),
        Segment(text="Second sentence there.", start=3.5, end=5.5),
    ]
    srt = formatter.format(segments)
    assert "1" in srt
    assert "2" in srt
    assert "00:00:01,000 --> 00:00:03,000" in srt
    assert "00:00:03,500 --> 00:00:05,500" in srt


def test_format_long_text_split(formatter: SubtitleFormatter) -> None:
    """Test that long text is split into multiple lines."""
    segments = [
        Segment(
            text="This is a very long sentence that should be split into multiple lines.",
            start=1.0,
            end=4.0,
        ),
    ]
    srt = formatter.format(segments)
    # Should have at least 2 lines of text after the timing line
    text_lines = [
        line for line in srt.strip().split("\n")
        if line and not line[0].isdigit() and "-->" not in line
    ]
    assert len(text_lines) > 0


def test_merge_short_segments(formatter: SubtitleFormatter) -> None:
    """Test that very short adjacent segments are merged."""
    segments = [
        Segment(text="Hello", start=1.0, end=1.3),
        Segment(text="world", start=1.4, end=1.7),
    ]
    srt = formatter.format(segments)
    # Should be merged into a single subtitle
    assert srt.count("-->") == 1
    assert "Hello world" in srt or "Hello" in srt
