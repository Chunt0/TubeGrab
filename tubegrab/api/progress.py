"""Resume positions, watched flags and plays."""

import math

from flask import Blueprint, jsonify, request

from ..config import _meta_lock, _now, PLAYS_FILE, PROGRESS_FILE
from ..events import _lib_bump, _publish
from ..meta import _plays, _progress, _rel, _write_json
from ..files import _safe_path
from ..db import _db
from ..httputil import _cached_json, _json_bytes
from ..playlists import _pl_ref


bp = Blueprint("progress", __name__)


@bp.get("/api/progress")
def api_progress_get():
    """Resume positions + watched flags, by library name (only items that have one)."""
    rows = _db().execute("SELECT f.name, p.pos, p.watched FROM progress p JOIN files f ON f.ref=p.ref "
                         "WHERE p.pos>0 OR p.watched=1").fetchall()
    return _cached_json(_json_bytes({r[0]: {"pos": r[1], "watched": bool(r[2])} for r in rows}))


@bp.post("/api/progress")
def api_progress():
    data = request.get_json(force=True, silent=True) or {}   # also sent with sendBeacon (text/plain)
    path = _safe_path(data.get("name") or "")
    if path is None:
        return jsonify(error="File not found."), 404
    ref = _pl_ref(path)
    with _meta_lock:
        prog = _progress()
        entry = prog.get(ref, {})
        if "pos" in data:
            try:
                pos = float(data["pos"])
            except (TypeError, ValueError):
                return jsonify(error="Bad position."), 400
            if not math.isfinite(pos):   # (Infinity would make progress.json unreadable to the UI)
                return jsonify(error="Bad position."), 400
            entry["pos"] = max(0.0, round(pos, 1))
        if "watched" in data:
            entry["watched"] = bool(data["watched"])
            if entry["watched"]:
                entry["pos"] = 0.0
        entry["at"] = _now()
        prog[ref] = entry
        _write_json(PROGRESS_FILE, prog)
    con = _db()
    old = con.execute("SELECT watched FROM progress WHERE ref=?", (ref,)).fetchone()
    con.execute("INSERT OR REPLACE INTO progress VALUES(?,?,?)",
                (ref, float(entry.get("pos") or 0), 1 if entry.get("watched") else 0))
    _publish("progress", {"name": _rel(path), "pos": entry.get("pos") or 0, "watched": bool(entry.get("watched"))})
    if bool(old and old[0]) != bool(entry.get("watched")):
        _lib_bump()   # watched counts on collection cards
    return jsonify(ok=True)


@bp.post("/api/played")
def api_played():
    """The player heard enough of an item to count it as played once more."""
    data = request.get_json(force=True, silent=True) or {}
    path = _safe_path(data.get("name") or "") if isinstance(data.get("name"), str) else None
    if path is None:
        return jsonify(error="File not found."), 404
    ref = _pl_ref(path)
    with _meta_lock:
        plays = _plays()
        entry = plays.get(ref, {})
        entry = {"n": int(entry.get("n") or 0) + 1, "last": _now()}
        plays[ref] = entry
        _write_json(PLAYS_FILE, plays)
    _db().execute("INSERT OR REPLACE INTO plays VALUES(?,?,?)", (ref, entry["n"], entry["last"]))
    return jsonify(plays=entry["n"])
