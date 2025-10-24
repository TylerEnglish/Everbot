from __future__ import annotations
import requests, pathlib, re, time, threading
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

_TOOLS   = load_all().get("tools", {}) or {}
_RL_CFG  = (_TOOLS.get("internet", {}) or {}).get("rate_limit", {}) or {}
_CT_ALLOW = set((_TOOLS.get("content_types_allowlist") or []))
_MAX_BYTES = int(_TOOLS.get("max_bytes_per_doc") or 0) or None

class _TokenBucket:
    def __init__(self, rps: float, burst: int, window_ms: int = 1000):
        self.rate = max(0.0, float(rps))
        self.capacity = max(0, int(burst))
        self.tokens = self.capacity
        self.window = max(1, int(window_ms))
        self.lock = threading.Lock()
        self.last = time.time()
    def take(self, n: int = 1):
        if self.rate <= 0 or self.capacity <= 0:
            return
        with self.lock:
            now = time.time()
            elapsed = now - self.last
            refill = elapsed * self.rate
            if refill >= 1.0:
                self.tokens = min(self.capacity, self.tokens + int(refill))
                self.last = now
            while self.tokens < n:
                need = (n - self.tokens) / max(1e-9, self.rate)
                time.sleep(min(0.050, need))
                now = time.time()
                elapsed = now - self.last
                refill = elapsed * self.rate
                if refill >= 1.0:
                    self.tokens = min(self.capacity, self.tokens + int(refill))
                    self.last = now
            self.tokens -= n

_BUCKET = _TokenBucket(
    float(_RL_CFG.get("rps_max") or 0),
    int(_RL_CFG.get("burst_max") or 0),
    int(_RL_CFG.get("window_ms") or 1000),
)

def _req(url: str) -> requests.Response:
    _BUCKET.take()
    # If we have a max size, try HEAD first to enforce a budget without downloading.
    if _MAX_BYTES:
        try:
            _BUCKET.take()
            h = requests.head(url, headers=_UA, timeout=15, allow_redirects=True)
            if h.ok:
                cl = h.headers.get("Content-Length")
                if cl and cl.isdigit() and int(cl) > _MAX_BYTES:
                    raise ValueError(f"Refusing to fetch > max_bytes_per_doc ({cl} > {_MAX_BYTES})")
        except Exception:
            # HEAD might be blocked; continue to GET and stream-guard.
            pass
    r = requests.get(url, headers=_UA, timeout=30, stream=bool(_MAX_BYTES))
    if _MAX_BYTES and r.ok and r.raw:
        # stream read up to _MAX_BYTES+1, then abort if exceeded
        content = b""
        for chunk in r.iter_content(chunk_size=65536):
            content += chunk
            if len(content) > _MAX_BYTES:
                r.close()
                raise ValueError("Downloaded payload exceeds max_bytes_per_doc")
        r._content = content
    return r

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
        if _CT_ALLOW:
            # normalize like 'application/pdf; charset=binary' → 'application/pdf'
            cmain = ct.split(";", 1)[0].strip() if ct else ""
            if cmain and cmain not in _CT_ALLOW:
                raise PermissionError(f"Disallowed content-type: {cmain}")
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
