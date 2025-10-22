from __future__ import annotations
import re

def _video_id_from_url(url: str) -> str | None:
    # supports https://youtu.be/<id>, https://www.youtube.com/watch?v=<id>, etc.
    pats = [
        r"youtu\.be/([A-Za-z0-9_-]{6,})",
        r"youtube\.com/watch\?.*v=([A-Za-z0-9_-]{6,})",
        r"youtube\.com/embed/([A-Za-z0-9_-]{6,})",
    ]
    for p in pats:
        m = re.search(p, url, re.I)
        if m: return m.group(1)
    return None

def fetch_youtube_transcript(url: str) -> tuple[str, str] | None:
    """
    Returns (title, text) or None if transcripts not available or lib missing.
    """
    vid = _video_id_from_url(url)
    if not vid: return None
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        tr = YouTubeTranscriptApi.get_transcript(vid, languages=["en"])
        text = " ".join([seg["text"] for seg in tr if seg.get("text")])
        title = f"YouTube Transcript {vid}"
        return title, text
    except Exception:
        return None
