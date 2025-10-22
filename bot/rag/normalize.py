from __future__ import annotations
import re

_whitespace = re.compile(r"[ \t\f\v]+")
_hardbreaks = re.compile(r"\s*\n\s*")
_dehyphen = re.compile(r"-\s*\n\s*")      # PDF hyphenation at line wrap
_multi_nl = re.compile(r"\n{3,}")

def normalize_text(text: str) -> str:
    if not text: return ""
    # 1) undo hyphenation across line breaks (co- \n operation -> cooperation)
    text = _dehyphen.sub("", text)
    # 2) collapse hard breaks inside paragraphs into spaces; keep paragraph breaks
    #    Heuristic: two or more newlines = paragraph; single newlines -> space
    text = text.replace("\r", "")
    text = re.sub(r"([^\n])\n(?!\n)", r"\1 ", text)
    # 3) normalize whitespace
    text = _whitespace.sub(" ", text)
    text = _multi_nl.sub("\n\n", text)
    return text.strip()

def strip_tail_references(text: str) -> str:
    """
    If a trailing 'References' section dominates the tail, strip it (heuristic).
    Keeps earlier references inline; we rely on provenance anyway.
    """
    if not text: return text
    m = re.search(r"\n\s*(references|bibliography)\s*\n", text, re.I)
    if not m: return text
    head, tail = text[:m.start()], text[m.start():]
    # only strip if tail is large and looks like citations
    if len(tail) > 2000 and re.search(r"\[\d+\]|\(arXiv|doi:|Proceedings|ACM|IEEE", tail, re.I):
        return head.strip()
    return text
