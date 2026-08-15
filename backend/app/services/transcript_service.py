import re
from typing import Tuple, Optional


class TranscriptService:
    @staticmethod
    def clean_subtitle_text(raw_text: str) -> Tuple[str, str]:
        """
        Parses raw SRT/VTT subtitle content, strips timestamps, formatting tags,
        and sound cues to return:
        - full_text: clean concatenated dialogue string
        - preview_text: first ~500 chars for UI inspection
        """
        if not raw_text:
            return "", ""

        # Remove WebVTT header if present
        text = re.sub(r"^WEBVTT.*?\n", "", raw_text, flags=re.DOTALL | re.IGNORECASE)

        # Remove timestamp lines (e.g., 00:01:23,456 --> 00:01:25,789)
        text = re.sub(r"\d{1,2}:\d{2}:\d{2}[,\.]\d{3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,\.]\d{3}.*", "", text)
        
        # Remove sequence number lines
        text = re.sub(r"^\d+\s*$", "", text, flags=re.MULTILINE)

        # Remove HTML / formatting tags (<b>, <i>, <font>, etc.)
        text = re.sub(r"<[^>]+>", "", text)

        # Remove sound effect cues like [Applause], (Groans), {Music}, [theme song playing]
        text = re.sub(r"\[.*?\]", "", text)
        text = re.sub(r"\(.*?\)", "", text)
        text = re.sub(r"\{.*?\}", "", text)

        # Clean multiple blank lines and normalize whitespace
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        full_text = " ".join(lines)
        
        # Normalize duplicate spaces
        full_text = re.sub(r"\s+", " ", full_text).strip()

        preview_text = full_text[:500] + ("..." if len(full_text) > 500 else "")
        return full_text, preview_text
