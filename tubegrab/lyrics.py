"""Song lyrics, synced where possible.

Looked up on LRCLIB (free, open lyrics database; only artist/title/album/length
are sent). Synced lyrics → <id>.lrc, plain only → <id>.lyrics.txt, nothing
found → <id>.nolyrics (so we don't ask again until you retry)."""

import json
import re
import threading
from pathlib import Path

from .config import AUDIO_EXTS, META_DIR, _now
from .meta import _ids_by_path, _iter_media, _keep_mtime, _meta_changed, _meta_for, _rel, _video_id
from .naming import _clean_channel
from .files import _probe



LRCLIB = "https://lrclib.net/api"
_LRC_LINE = re.compile(r"\[(\d+):(\d{2}(?:[.:]\d{1,3})?)\]")
_lyrics_lock = threading.Lock()
_lrclib_last = 0.0


def _lrclib(path: str, params: dict):
    import urllib.error
    import urllib.parse
    import urllib.request
    import time
    global _lrclib_last
    url = f"{LRCLIB}/{path}?{urllib.parse.urlencode({k: v for k, v in params.items() if v})}"
    req = urllib.request.Request(url, headers={"User-Agent": "TubeGrab (self-hosted music library)"})
    for attempt in range(4):
        wait = _lrclib_last + 1.5 - time.monotonic()   # ≤ 1 request / 1.5 s
        if wait > 0:
            time.sleep(wait)
        _lrclib_last = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):   # no such song / a query it won't answer (a 63-minute mix)
                return None
            if e.code == 429 and attempt < 3:   # slow down as asked
                time.sleep(float(e.headers.get("Retry-After") or 30))
                continue
            raise


def _parse_lrc(text: str) -> list[dict]:
    """'[01:02.50] line' → [{start, end, text}] (empty lines end the previous one)."""
    rows = []
    for line in text.splitlines():
        stamps = _LRC_LINE.findall(line)
        if not stamps:
            continue
        words = _LRC_LINE.sub("", line).strip()
        for mi, se in stamps:
            rows.append((int(mi) * 60 + float(se.replace(":", ".")), words))
    rows.sort(key=lambda r: r[0])
    segs = []
    for k, (t, words) in enumerate(rows):
        end = rows[k + 1][0] if k + 1 < len(rows) else t + 6
        if words:
            segs.append({"start": round(t, 2), "end": round(end, 2), "text": words})
    return segs


def _embed_lyrics(path: Path, text: str) -> None:
    if path.suffix.lower() != ".m4a" or not text:
        return
    try:
        from mutagen.mp4 import MP4
        restore = _keep_mtime(path)
        f = MP4(str(path))
        f["\xa9lyr"] = [text]
        f.save()
        restore()
    except Exception as exc:  # noqa: BLE001
        print(f"[tubegrab] lyrics embed failed for {path.name}: {exc}")


def fetch_lyrics(path: Path, force: bool = False) -> str | None:
    """Find lyrics for a library song. Returns "synced", "plain" or None."""
    vid = _video_id(path)
    if not vid or path.suffix.lower() not in AUDIO_EXTS:
        return None
    lrc, txt, miss = (META_DIR / f"{vid}{ext}" for ext in (".lrc", ".lyrics.txt", ".nolyrics"))
    if not force and (lrc.exists() or txt.exists() or miss.exists()):
        return "synced" if lrc.exists() else "plain" if txt.exists() else None
    meta = _meta_for(vid)
    artist = meta.get("album_artist") or meta.get("artist") or _clean_channel(meta.get("uploader"))
    track = meta.get("track") or meta.get("title")
    dur = meta.get("duration") or _probe(path)["duration"]
    if not artist or not track:
        return None
    with _lyrics_lock:   # be gentle with the free service: one lookup at a time
        rec = _lrclib("get", {"artist_name": artist, "track_name": track,
                              "album_name": meta.get("album"), "duration": round(dur) if dur else None})
        if not rec:   # no exact match: search, prefer the closest length
            found = _lrclib("search", {"artist_name": artist, "track_name": track}) or []
            found = [r for r in found if r.get("plainLyrics") or r.get("syncedLyrics")]
            if dur:
                found.sort(key=lambda r: abs((r.get("duration") or 0) - dur))
            rec = found[0] if found else None
    for p in (lrc, txt, miss):
        p.unlink(missing_ok=True)
    if not rec or rec.get("instrumental") or not (rec.get("plainLyrics") or rec.get("syncedLyrics")):
        miss.write_text(_now())
        _meta_changed(walk=False, vids=[vid])
        return None
    # synced lines only line up when it's the same recording (music videos often add intros)
    same_cut = not dur or abs((rec.get("duration") or 0) - dur) <= 3
    plain = rec.get("plainLyrics") or "\n".join(sg["text"] for sg in _parse_lrc(rec.get("syncedLyrics") or ""))
    if rec.get("syncedLyrics") and same_cut:
        lrc.write_text(rec["syncedLyrics"], "utf-8")
        kind = "synced"
    else:
        txt.write_text(plain, "utf-8")
        kind = "plain"
    _embed_lyrics(path, plain)
    _meta_changed(walk=False, vids=[vid])
    return kind


def _fetch_lyrics_bg(path: Path) -> None:
    def run():
        try:
            fetch_lyrics(path)
        except Exception as exc:  # noqa: BLE001 — offline etc.; retried on next start
            print(f"[tubegrab] lyrics lookup failed for {path.name}: {exc}")
    threading.Thread(target=run, daemon=True, name="lyrics").start()


def backfill_lyrics() -> None:
    """Look up lyrics for songs that haven't been checked yet."""
    by_path = _ids_by_path()
    for path in list(_iter_media()):
        if _rel(path).split("/")[0] != "Music" or path.suffix.lower() not in AUDIO_EXTS:
            continue
        vid = _video_id(path, by_path)
        if not vid or any((META_DIR / f"{vid}{e}").exists() for e in (".lrc", ".lyrics.txt", ".nolyrics")):
            continue
        try:
            fetch_lyrics(path)
        except Exception as exc:  # noqa: BLE001 — offline: stop, try again next start
            print(f"[tubegrab] lyrics backfill paused: {exc}")
            return
