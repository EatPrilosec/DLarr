import pytest
from backend.app.services.transcript_service import TranscriptService


def test_transcript_cleaning():
    raw_srt = """1
00:00:01,000 --> 00:00:04,000
<i>[Upbeat music playing]</i>
Hello, welcome to <b>DLarr</b>!

2
00:00:04,500 --> 00:00:08,000
(Audience cheering)
In this episode, we test AI matching.
"""
    full, preview = TranscriptService.clean_subtitle_text(raw_srt)
    assert "Hello, welcome to DLarr!" in full
    assert "In this episode, we test AI matching." in full
    assert "00:00:01" not in full
    assert "[Upbeat music playing]" not in full
    assert "(Audience cheering)" not in full
    assert "<b>" not in full
    assert len(preview) <= 503
