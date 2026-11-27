"""Video captions as timed text.

YouTube captions (saved as .meta/<id>.<lang>.vtt) become clean timed lines for
the player's CC, the transcript panel and library-wide search."""

import re
from pathlib import Path

from .config import META_DIR, SUB_LANGS



_TS_RE = re.compile(r"(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})")
_transcripts: dict[str, tuple[float, list]] = {}   # vtt path → (mtime, segments)


def _ts(v: str) -> float:
    m = _TS_RE.match(v.strip())
    if not m:
        return 0.0
    h, mi, se, ms = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(se) + int(ms) / 1000


_CUE_SPLIT = re.compile(r"\n\s*\n")
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _parse_vtt(text: str) -> list[dict]:
    """Cues → [{start, end, text}], collapsing YouTube's rolling auto-captions
    (each cue repeats the previous line) into one entry per spoken line."""
    import html
    segs: list[dict] = []
    last: list[str] = []
    for block in _CUE_SPLIT.split(text.replace("\r", "")):
        lines = block.strip().split("\n")
        i = next((k for k, l in enumerate(lines) if "-->" in l), None)
        if i is None:
            continue
        a, b = lines[i].split("-->")[:2]
        start, end = _ts(a), _ts(b.split()[0] if b.split() else b)
        if end - start < 0.05:          # zero-length "transition" cues
            continue
        body = []
        for l in lines[i + 1:]:
            if "<" in l:
                l = _TAG.sub("", l)
            if "&" in l:
                l = html.unescape(l)
            l = _WS.sub(" ", l).strip()
            if l:
                body.append(l)
        new = [l for l in body if l not in last]
        if new:
            segs.append({"start": round(start, 2), "end": round(end, 2), "text": " ".join(new)})
        last = body or last
    for k in range(len(segs) - 1):      # a line lasts until the next one starts (max 8 s)
        segs[k]["end"] = round(min(segs[k + 1]["start"], segs[k]["start"] + 8), 2)
    return segs


def _sub_file(vid: str | None) -> Path | None:
    if not vid:
        return None
    for lang in SUB_LANGS:
        p = META_DIR / f"{vid}.{lang}.vtt"
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


def _transcript(vid: str | None) -> list[dict]:
    path = _sub_file(vid)
    if not path:
        return []
    mtime = path.stat().st_mtime
    cached = _transcripts.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        segs = _parse_vtt(path.read_text("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        segs = []
    _transcripts[str(path)] = (mtime, segs)
    while len(_transcripts) > 16:   # only the few being watched; search uses the index
        _transcripts.pop(next(iter(_transcripts)))
    return segs
