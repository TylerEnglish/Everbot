from __future__ import annotations
import re, math, collections
from typing import List, Dict, Any, Tuple
from bot.llm.provider import ask as llm_ask, NotConfigured

_STOP = set("""
the a an and or for of to in on with from as by is are was were be been being this that these those which who whom whose
at into over under up down out about above below between across per via it its it's if then else when while because
""".split())

def _sentences(txt: str) -> List[str]:
    # lightweight sentence splitter
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", txt.strip())
    return [p.strip() for p in parts if p.strip()]

def _score_sentence(sent: str, topic_terms: List[str]) -> float:
    words = [w.lower() for w in re.findall(r"[a-z0-9]+", sent)]
    counts = collections.Counter(w for w in words if w not in _STOP and len(w) > 2)
    # topic-aware boost
    boost = 1.0 + 0.2 * sum(1 for t in topic_terms if t in words)
    return sum((1.0 + math.log1p(c)) for _, c in counts.items()) * boost

def distill_bullets(
    topic: str,
    retrieved: List[Tuple[str, str]],  # [(chunk_id, chunk_text)]
    want: int = 10
) -> List[Dict[str, Any]]:
    """
    Returns bullets: [{"text": "...", "sources": ["<chunk_id>", ...]}]
    Uses LLM if configured; otherwise extracts top key sentences.
    """
    topic_terms = [w.lower() for w in re.findall(r"[a-z0-9]+", topic)]
    # 1) Try LLM summarization for high-quality bullets + provenance hints
    joined = "\n\n".join([f"[{cid}] {txt}" for cid, txt in retrieved[:8]])
    prompt = (
        "Distill the key tactics/facts as short bullets (<= 18 words each). "
        "Each bullet MUST include bracketed source chunk ids like [doc:idx]. "
        f"Topic: {topic}\n\nSources:\n{joined}\n\nBullets:\n"
    )
    try:
        out = llm_ask(prompt, max_tokens=256, temperature=0.0)
        # parse lines like "- text [doc:0][doc:1]"
        bullets = []
        for line in out.splitlines():
            line = line.strip(" -*\t")
            if not line: continue
            ids = re.findall(r"\[([^\]]+:\d+)\]", line)
            text = re.sub(r"\s*\[[^\]]+:\d+\]\s*", " ", line).strip()
            if text:
                bullets.append({"text": text, "sources": sorted(set(ids))})
        if bullets:
            return bullets[:want]
    except NotConfigured:
        pass
    except Exception:
        pass

    # 2) Fallback: extractive bullets by sentence scoring + provenance mapping
    scored = []
    for cid, txt in retrieved:
        for s in _sentences(txt):
            scored.append(( _score_sentence(s, topic_terms), s, cid ))
    scored.sort(reverse=True)
    bullets = []
    seen = set()
    for score, sent, cid in scored:
        if len(bullets) >= want: break
        key = sent.lower()
        if key in seen: continue
        seen.add(key)
        bullets.append({"text": sent, "sources": [cid]})
    return bullets
