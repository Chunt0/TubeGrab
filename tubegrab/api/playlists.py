"""Your playlists: list, create, edit, delete, pictures."""

import re
import uuid

from flask import Blueprint, jsonify, request

from ..config import _meta_lock, _now, PL_COVERS_DIR, PLAYLISTS_FILE
from ..meta import _index, _write_json
from ..httputil import _cached_json, _json_bytes
from ..images import BAD_PICTURE, _save_picture, _send_image
from ..playlists import _pl_all, _pl_out, _pl_refs, _pl_text, _playlists_out


bp = Blueprint("playlists", __name__)


@bp.get("/api/playlists")
def api_playlists():
    return _cached_json(_json_bytes({"playlists": _playlists_out()}))


@bp.get("/api/playlists/<pid>")
def api_playlist(pid: str):
    p = _pl_all().get(pid)
    if p is None:
        return jsonify(error="Playlist not found."), 404
    return _cached_json(_json_bytes({"playlist": _pl_out(pid, p, _index(), full=True)}))


@bp.post("/api/playlists")
def api_playlist_create():
    data = request.get_json(force=True, silent=True) or {}
    p: dict = {"items": [], "cover": None, "created": _now()}
    err = _pl_text({"title": data.get("title"), "description": data.get("description")}, p)
    if err:
        return jsonify(error=err), 400
    refs = _pl_refs(data.get("items") or [])
    if refs is None:
        return jsonify(error="Some items weren't found."), 400
    p["items"], p["updated"] = refs, _now()
    pid = uuid.uuid4().hex[:10]
    with _meta_lock:
        pls = _pl_all()
        pls[pid] = p
        _write_json(PLAYLISTS_FILE, pls)
    return jsonify(playlist=_pl_out(pid, p, _index()))


@bp.patch("/api/playlists/<pid>")
def api_playlist_update(pid: str):
    """Any of: title, description, items (full ordered list of names),
    cover_item (a name, or null to go back to the automatic cover)."""
    data = request.get_json(force=True, silent=True) or {}
    with _meta_lock:
        pls = _pl_all()
        p = pls.get(pid)
        if p is None:
            return jsonify(error="Playlist not found."), 404
        err = _pl_text(data, p)
        if err:
            return jsonify(error=err), 400
        if "items" in data:
            refs = _pl_refs(data["items"])
            if refs is None:
                return jsonify(error="Some items weren't found."), 400
            p["items"] = refs
        if "cover_item" in data:
            name = data["cover_item"]
            if name is None:
                p["cover"] = None
            else:
                refs = _pl_refs([name])
                if not refs:
                    return jsonify(error="That item wasn't found."), 400
                p["cover"] = {"item": refs[0]}
            (PL_COVERS_DIR / f"{pid}.jpg").unlink(missing_ok=True)
        p["updated"] = _now()
        _write_json(PLAYLISTS_FILE, pls)
    return jsonify(playlist=_pl_out(pid, p, _index()))


@bp.delete("/api/playlists/<pid>")
def api_playlist_delete(pid: str):
    """Deletes the playlist only — never the files in it."""
    with _meta_lock:
        pls = _pl_all()
        if pls.pop(pid, None) is None:
            return jsonify(error="Playlist not found."), 404
        _write_json(PLAYLISTS_FILE, pls)
    (PL_COVERS_DIR / f"{pid}.jpg").unlink(missing_ok=True)
    return jsonify(ok=True)


@bp.post("/api/playlists/<pid>/cover")
def api_playlist_upload_cover(pid: str):
    """Upload a picture: cropped square and stored as a JPEG."""
    if pid not in _pl_all():
        return jsonify(error="Playlist not found."), 404
    up = request.files.get("file")
    if up is None or not up.filename:
        return jsonify(error="Choose an image file."), 400
    if not _save_picture(up, PL_COVERS_DIR / f"{pid}.jpg"):
        return jsonify(error=BAD_PICTURE), 400
    with _meta_lock:
        pls = _pl_all()
        if pid in pls:
            pls[pid]["cover"] = {"upload": uuid.uuid4().hex[:8]}
            pls[pid]["updated"] = _now()
            _write_json(PLAYLISTS_FILE, pls)
    return jsonify(playlist=_pl_out(pid, pls[pid], _index()))


@bp.get("/playlist-cover/<pid>")
def playlist_cover(pid: str):
    if not re.fullmatch(r"[0-9a-f]{10}", pid):
        return jsonify(error="Not found."), 404
    path = PL_COVERS_DIR / f"{pid}.jpg"
    if not path.exists():
        return jsonify(error="Not found."), 404
    return _send_image(path, 31536000 if request.args.get("v") else 86400, "image/jpeg")
