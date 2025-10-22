from __future__ import annotations
import requests, pathlib, re
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from PyPDF2 import PdfReader
from bot.rag.normalize import normalize_text, strip_tail_references
from bot.rag.transcripts import fetch_youtube_transcript
from bot.rules.loader import load_all

_UA = {"User-Agent": "Everabot/0.1 (+https://example.invalid)"}

# allow-list pulled from ruleset/tools.yaml
_ALLOWED = {h.lower() for h in (load_all().get("tools", {}).get("net", {}).get("allowed_hosts", []) or [])}

# match mistaken RFC PDF paths like https://www.rfc-editor.org/rfc/rfc3284.pdf
_RFC_PDF_MISPATH = re.compile(r"^https?://(?:www\.)?rfc-editor\.org/rfc/rfc(\d+)\.pdf$", re.I)

def _req(url: str) -> requests.Response:
    return requests.get(url, headers=_UA, timeout=30)

def _try_rfc_fallback(orig_url: str, resp: requests.Response) -> tuple[str, requests.Response] | tuple[None, None]:
    if resp.status_code != 404:
        return (None, None)
    m = _RFC_PDF_MISPATH.match(orig_url)
    if not m:
        return (None, None)
    num = m.group(1)
    alternates = [
        f"https://www.rfc-editor.org/rfc/pdfrfc/rfc{num}.txt.pdf",
        f"https://www.rfc-editor.org/rfc/rfc{num}.html",
        f"https://datatracker.ietf.org/doc/html/rfc{num}",
    ]
    for alt in alternates:
        r2 = _req(alt)
        if r2.ok:
            return (alt, r2)
    return (None, None)

def fetch(url_or_path: str) -> tuple[str, str]:
    """
    Returns (title, text). If local path to .pdf/.html/.txt, we parse locally.
    If URL, we fetch politely (single GET). YouTube: transcript fast-path.
    """
    # 0) YouTube transcript
    if re.match(r"^https?://(www\.)?(youtube\.com|youtu\.be)/", url_or_path, re.I):
        yt = fetch_youtube_transcript(url_or_path)
        if yt:
            title, text = yt
            return (title, strip_tail_references(normalize_text(text)))

    # 1) Remote URL
    if re.match(r"^https?://", url_or_path, re.I):
        host = (urlparse(url_or_path).hostname or "").lower()
        if _ALLOWED and host not in _ALLOWED:
            raise PermissionError(f"Host '{host}' not in allowed_hosts policy.")
        r = _req(url_or_path)
        if not r.ok:
            alt_url, alt_resp = _try_rfc_fallback(url_or_path, r)
            if alt_resp:
                url_or_path, r = alt_url, alt_resp
        r.raise_for_status()

        ct = (r.headers.get("content-type") or "").lower()
        if "pdf" in ct or url_or_path.lower().endswith(".pdf"):
            return (url_or_path, _pdf_bytes_to_text(r.content))
        elif "html" in ct or "<html" in r.text.lower():
            return _html_to_text(r.text, url_or_path)
        else:
            return (url_or_path, r.text)

    # 2) Local file
    p = pathlib.Path(url_or_path)
    if p.suffix.lower() == ".pdf":
        return (p.name, _pdf_bytes_to_text(p.read_bytes()))
    if p.suffix.lower() in {".html", ".htm"}:
        return _html_to_text(p.read_text(encoding="utf-8", errors="replace"), str(p))
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

def _html_to_text(html: str, title_fallback: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string.strip() if soup.title and soup.title.string else title_fallback)
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ").split())
    return (title, text)
