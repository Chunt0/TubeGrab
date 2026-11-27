"""Your playlists (the data; routes are in api/playlists).

Your own playlists: ordered references to library items ("<id>:<kind>", which
survive renames and re-filing, or "path:<rel>" for files without an id). The
files stay where they are, so one track can be in any number of playlists."""

import re
from pathlib import Path

from .config import DOWNLOAD_DIR, _meta_lock, PLAYLISTS_FILE
from .meta import _ids_by_path, _index, _kind, _read_json, _rel, _video_id, _write_json
from .files import _safe_path
from .db import _db, _rows_by_names



def _pl_all() -> dict:
    return _read_json(PLAYLISTS_FILE, {})


def _pl_ref(path: Path, by_path: dict | None = None) -> str:
    vid = _video_id(path, by_path)
    return f"{vid}:{_kind(path)}" if vid else f"path:{_rel(path)}"


def _pl_resolve(ref: str, idx: dict) -> str | None:
    rel = ref[5:] if ref.startswith("path:") else idx.get(ref)
    return rel if rel and (DOWNLOAD_DIR / rel).is_file() else None


def _tile(f: dict) -> dict:
    return {"name": f["name"], "tv": f["tv"], "color": f["color"], "kind": f["kind"]}


def _pl_out(pid: str, p: dict, idx: dict, full: bool = False) -> dict:
    names = [r for r in (_pl_resolve(ref, idx) for ref in p.get("items", [])) if r]
    rows = _rows_by_names(_db(), names)
    files = [rows[n] for n in names if n in rows]
    cover = p.get("cover") or {}
    cover_item = _pl_resolve(cover["item"], idx) if cover.get("item") else None
    seen: set = set()
    mosaic = []   # one tile per album (its tracks share the art), every video its own
    for f in files:
        k = f["collection"] if f["section"] == "Music" and f["collection"] else f["name"]
        if k not in seen:
            seen.add(k)
            mosaic.append(_tile(f))
            if len(mosaic) == 4:
                break
    out = {
        "id": pid, "title": p.get("title") or "Untitled", "description": p.get("description") or "",
        "items": [f["name"] for f in files], "created": p.get("created"), "updated": p.get("updated"),
        "count": len(files), "duration": sum(f["duration"] or 0 for f in files),
        "cover_item": cover_item, "cover_tile": _tile(rows[cover_item]) if cover_item in rows else None,
        "cover_upload": cover.get("upload"),   # version string → /playlist-cover/<id>?v=
        "mosaic": mosaic,
    }
    if full:
        out["files"] = files
    return out


def _playlists_out() -> list[dict]:
    idx = _index()
    out = [_pl_out(pid, p, idx) for pid, p in _pl_all().items()]
    return sorted(out, key=lambda p: p["updated"] or "", reverse=True)


def _playlists_drop_missing() -> None:
    """Forget references to files that no longer exist anywhere."""
    with _meta_lock:
        pls, idx = _pl_all(), _index()
        changed = False
        for p in pls.values():
            keep = [r for r in p.get("items", []) if _pl_resolve(r, idx)]
            if keep != p.get("items", []):
                p["items"], changed = keep, True
            if (p.get("cover") or {}).get("item") and not _pl_resolve(p["cover"]["item"], idx):
                p["cover"], changed = None, True
        if changed:
            _write_json(PLAYLISTS_FILE, pls)


def _pl_refs(names) -> list[str] | None:
    """Library names from the client → unique references (None if malformed)."""
    if not isinstance(names, list):
        return None
    by_path = _ids_by_path()
    refs: list[str] = []
    for n in names:
        path = _safe_path(n) if isinstance(n, str) else None
        if path is None:
            return None
        ref = _pl_ref(path, by_path)
        if ref not in refs:
            refs.append(ref)
    return refs


def _pl_text(data: dict, p: dict) -> str | None:
    """Apply title/description; returns an error message or None."""
    if "title" in data:
        title = re.sub(r"\s+", " ", str(data.get("title") or "")).strip()
        if not title:
            return "Give the playlist a name."
        p["title"] = title[:120]
    if "description" in data:
        p["description"] = str(data.get("description") or "").strip()[:1000]
    return None
