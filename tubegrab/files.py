"""Library paths and what is in a file: safe path resolution (nothing outside
the library is ever served), titles from file names, ffprobe, poster frames,
collection kinds from folder paths."""

import json
import os
import re
import subprocess
from pathlib import Path

from .config import DOWNLOAD_DIR, INDEX_PREFIX_RE, META_DIR, SECTIONS, SINGLES, YEAR_RE
from .meta import _read_json


def _safe_path(name: str, *, dir_ok: bool = False) -> Path | None:
    """Resolve a library path like 'Music/A/B/file.m4a', refusing anything
    outside downloads/ or inside hidden folders."""
    if not name or name.startswith("/") or "\\" in name:
        return None
    parts = name.split("/")
    if any(not p or p.startswith(".") for p in parts):
        return None
    path = (DOWNLOAD_DIR / name).resolve()
    try:
        path.relative_to(DOWNLOAD_DIR)
    except ValueError:
        return None
    if dir_ok:
        return path if path.is_dir() and path != DOWNLOAD_DIR else None
    return path if path.is_file() else None


def _display_title(name: str) -> str:
    """'03 - Song [dQw4w9WgXcQ].m4a' → 'Song'."""
    stem = Path(name).stem
    stem = re.sub(r"\s*\[[A-Za-z0-9_-]{11}\]$", "", stem) or stem
    return INDEX_PREFIX_RE.sub("", stem)


def _generate_thumb(path: Path, out: Path) -> None:
    """Grab a frame with ffmpeg; try 1s in, fall back to the first frame."""
    for seek in ("1", "0"):
        try:
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", seek,
                 "-i", str(path), "-frames:v", "1", "-vf", "scale=640:-2",
                 "-q:v", "4", "-y", str(out)],
                timeout=30, check=True,
            )
            if out.exists() and out.stat().st_size > 0:
                return
        except Exception:
            continue  # noqa: S110 — fall through to the placeholder


# ffprobe is slow on a NAS CPU. The library index keeps each file's duration and
# height; this cache (seeded from probe.json, written by versions before the
# index) covers files the index hasn't seen yet.
PROBE_FILE = META_DIR / "probe.json"
_probe_cache: dict[str, dict] = _read_json(PROBE_FILE, {})


def _probe(path: Path | str, st: os.stat_result | None = None) -> dict:
    """Duration + video height via ffprobe, cached per path+mtime."""
    st = st or os.stat(path)
    key = f"{path}|{int(st.st_mtime)}|{st.st_size}"
    if key in _probe_cache:
        return _probe_cache[key]
    res: dict = {"duration": None, "height": None}
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=height,codec_type:stream_disposition=attached_pic",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=15,
        ).stdout
        data = json.loads(out or "{}")
        dur = (data.get("format") or {}).get("duration")
        res["duration"] = round(float(dur), 2) if dur else None
        for s in data.get("streams", []):
            if s.get("codec_type") == "video" and s.get("height") and not (s.get("disposition") or {}).get("attached_pic"):
                res["height"] = int(s["height"])
                break
    except Exception:  # noqa: BLE001 — cosmetic
        pass
    _probe_cache[key] = res
    return res


def _collection_info(key: str) -> dict:
    """Describe a collection folder from its path."""
    parts = key.split("/")
    section = parts[0] if parts[0] in SECTIONS and len(parts) >= 2 else "Other"
    name = parts[-1]
    info = {"key": key, "section": section, "artist": None, "year": None, "title": name}
    if section == "Music" and len(parts) >= 3:
        info["artist"] = parts[1]
        if name == SINGLES:
            info.update(kind="singles", title="Singles")
        else:
            m = YEAR_RE.search(name)
            info.update(kind="album", title=YEAR_RE.sub("", name), year=m.group(1) if m else None)
    elif section == "Videos":
        info["kind"] = "channel"
    elif section == "Playlists":
        info["kind"] = "playlist"
    else:
        info["kind"] = "folder"
    return info


def _clean_chapters(meta: dict) -> list[dict] | None:
    """Real chapters only (not the single 'whole video' one cutting sponsors leaves)."""
    ch = meta.get("chapters") or []
    if len(ch) < 2 or all(c.get("title") == meta.get("title") for c in ch):
        return None
    return [{"start": round(c.get("start_time") or 0, 2), "title": c.get("title") or ""} for c in ch]
