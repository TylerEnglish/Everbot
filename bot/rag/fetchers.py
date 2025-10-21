from __future__ import annotations
import requests, pathlib, re
from bs4 import BeautifulSoup
from PyPDF2 import PdfReader

_UA = {"User-Agent": "Everabot/0.1 (+https://example.invalid)"}

def fetch(url_or_path: str) -> tuple[str,str]:
    """
    Returns (title, text). If local path to .pdf/.html/.txt, we parse locally.
    If URL, we fetch politely (no crawling; single GET).
    """
    if re.match(r"^https?://", url_or_path, re.I):
        r = requests.get(url_or_path, headers=_UA, timeout=30)
        r.raise_for_status()
        ct = (r.headers.get("content-type") or "").lower()
        if "pdf" in ct or url_or_path.lower().endswith(".pdf"):
            return (url_or_path, _pdf_bytes_to_text(r.content))
        elif "html" in ct or "<html" in r.text.lower():
            return _html_to_text(r.text, url_or_path)
        else:
            return (url_or_path, r.text)
    # local file
    p = pathlib.Path(url_or_path)
    if p.suffix.lower() == ".pdf":
        return (p.name, _pdf_bytes_to_text(p.read_bytes()))
    if p.suffix.lower() in {".html", ".htm"}:
        return _html_to_text(p.read_text(encoding="utf-8", errors="replace"), str(p))
    # txt or anything else
    return (p.name, p.read_text(encoding="utf-8", errors="replace"))

def _pdf_bytes_to_text(b: bytes) -> str:
    import io
    reader = PdfReader(io.BytesIO(b))
    out = []
    for page in reader.pages:
        try:
            out.append(page.extract_text() or "")
        except Exception:
            pass
    return "\n".join(out)

def _html_to_text(html: str, title_fallback: str) -> tuple[str,str]:
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string.strip() if soup.title and soup.title.string else title_fallback)
    # crude readability: drop scripts/styles
    for tag in soup(["script","style","noscript"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ").split())
    return (title, text)
