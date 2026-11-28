"""Library routes: the library overview, file lists, the watch page, shuffle,
search and the /api/events stream."""

import hashlib
import json
import re
import sqlite3

from flask import Blueprint, Response, jsonify, request

from ..config import DOWNLOAD_DIR
from ..events import _BOOT, _bus, _bus_last, _bus_prog
from ..meta import _index
from ..files import _collection_info
from ..db import _db, _file_out, _order_sql, _OUT_COLS, PLAYS_LAST, PLAYS_N, _rows_by_names, _search_key
from ..httputil import _cached_json, _json_bytes
from ..playlists import _pl_all, _pl_resolve
from ..favorites import favorite_refs
from ..index import _indexer
from ..jobs import _jobs_payload
from .. import events


bp = Blueprint("library", __name__)


_lib_cache: dict = {"key": None, "body": b"", "gz": b"", "etag": ""}


@bp.get("/api/library")
def api_library():
    """Collections (every album, channel, playlist folder) + totals: small enough to
    send whole, so grids, artists and sorting work instantly on the device."""
    key = events._lib_version
    c = _lib_cache
    if c["key"] != key:
        con = _db()
        colors = dict(con.execute("SELECT tv, color FROM colors WHERE color IS NOT NULL").fetchall())
        cols = []
        for (data,) in con.execute("SELECT data FROM collections ORDER BY updated DESC"):
            d = json.loads(data)
            for cv in d["covers"]:
                cv["color"] = colors.get(cv["tv"])
            cols.append(d)
        watched = dict(con.execute(
            "SELECT f.dir, COUNT(*) FROM files f JOIN progress p ON p.ref=f.ref "
            "WHERE p.watched=1 AND f.kind='video' AND f.dir!='' GROUP BY f.dir").fetchall())
        stats = {"music": {"count": 0, "size": 0, "duration": 0, "audio": 0},
                 "videos": {"count": 0, "size": 0, "duration": 0, "audio": 0}}
        for r in con.execute("SELECT section='Music' AS m, COUNT(*), COALESCE(SUM(size),0), "
                             "COALESCE(SUM(duration),0), SUM(kind='audio') FROM files GROUP BY m"):
            stats["music" if r[0] else "videos"] = {"count": r[1], "size": r[2], "duration": r[3], "audio": r[4] or 0}
        body = _json_bytes({"dir": str(DOWNLOAD_DIR), "ready": _indexer.ready.is_set(),
                            "collections": cols, "watched": watched, "stats": stats})
        import gzip
        c.update(key=key, body=body, gz=gzip.compress(body, 5), etag=hashlib.sha1(body).hexdigest()[:20])
    return _cached_json(c["body"], c["etag"], c["gz"])


@bp.get("/api/files")
def api_files():
    """Library files, filtered / sorted / paged in the database.
    dir=<collection> · section=Music|videos · kind=audio|video · q=<words> · fav=1
    sort=new|old|title|artist|plays|played|size|long|auto · offset · limit · only=names"""
    a = request.args
    where, args = ["1=1"], []
    d = a.get("dir")
    if d is not None:
        where.append("f.dir=?")
        args.append(d)
    if a.get("section") == "Music":
        where.append("f.section='Music'")
    elif a.get("section") == "videos":
        where.append("f.section!='Music'")
    if a.get("kind") in ("audio", "video"):
        where.append("f.kind=?")
        args.append(a["kind"])
    if a.get("fav"):   # hearted songs only
        refs = favorite_refs()
        where.append(f"f.ref IN ({','.join('?' * len(refs))})" if refs else "0")
        args.extend(refs)
    for tok in _search_key(a.get("q") or "").split()[:8]:
        where.append("f.search LIKE ? ESCAPE '\\'")
        args.append("%" + tok.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    sort = a.get("sort") or "auto"
    orders = {"new": "f.mtime DESC", "old": "f.mtime ASC", "title": "f.sort_title", "size": "f.size DESC",
              "long": "COALESCE(f.duration,0) DESC",
              "artist": "f.artist IS NULL, f.artist COLLATE NOCASE, f.album COLLATE NOCASE, f.idx, f.sort_title",
              "plays": f"COALESCE({PLAYS_N},0) DESC", "played": f"COALESCE({PLAYS_LAST},'') DESC"}
    order = orders.get(sort) or (_order_sql(_collection_info(d)["kind"]) if d else "f.mtime DESC")
    w = " AND ".join(where)
    con = _db()
    if a.get("only") == "names":
        names = [r[0] for r in con.execute(f"SELECT f.name FROM files f WHERE {w} ORDER BY {order}, f.name LIMIT 20000", args)]
        return _cached_json(_json_bytes({"names": names}))
    try:
        limit = max(1, min(int(a.get("limit") or 60), 2000))
        offset = max(0, int(a.get("offset") or 0))
    except ValueError:
        return jsonify(error="Bad paging."), 400
    total = con.execute(f"SELECT COUNT(*) FROM files f WHERE {w}", args).fetchone()[0]
    rows = con.execute(f"SELECT {_OUT_COLS} FROM files f LEFT JOIN colors k ON k.tv=f.tv WHERE {w} "
                       f"ORDER BY {order}, f.name LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
    return _cached_json(_json_bytes({"total": total, "files": [_file_out(r) for r in rows]}))


@bp.get("/api/watch/<path:name>")
def api_watch(name: str):
    """One file plus what plays around it: the rest of its playlist (?list=) or
    album, which is where playing stops; in other folders (channels, singles…)
    the rest of the folder, then the newest from the same section."""
    con = _db()
    r = con.execute(f"SELECT {_OUT_COLS}, f.chapters FROM files f LEFT JOIN colors k ON k.tv=f.tv WHERE f.name=?",
                    (name,)).fetchone()
    if r is None:
        return jsonify(error="File not found."), 404
    f = _file_out(r, r["chapters"] or "")
    crow = con.execute("SELECT data FROM collections WHERE key=?", (f["collection"],)).fetchone() if f["collection"] else None
    coll = json.loads(crow[0]) if crow else None
    out: dict = {"file": f, "collection": coll, "playlist": None, "prev": None, "next": []}
    pid = request.args.get("list")
    pl = _pl_all().get(pid) if pid else None
    if pl is not None:
        idx = _index()
        names = [n for n in (_pl_resolve(ref, idx) for ref in pl.get("items", [])) if n]
        if name in names:
            i = names.index(name)
            rows = _rows_by_names(con, names[max(0, i - 1):i + 201])
            out["playlist"] = {"id": pid, "title": pl.get("title") or "Untitled", "position": i, "total": len(names)}
            out["next"] = [rows[n] for n in names[i + 1:i + 201] if n in rows]
            out["prev"] = rows.get(names[i - 1]) if i > 0 else None
            return _cached_json(_json_bytes(out))
    after: list[dict] = []
    kind = None
    if f["collection"]:
        kind = coll["kind"] if coll else _collection_info(f["collection"])["kind"]
        members = [x["name"] for x in con.execute(
            f"SELECT f.name FROM files f WHERE f.dir=? ORDER BY {_order_sql(kind)}, f.name", (f["collection"],))]
        i = members.index(name) if name in members else -1
        want = members[i + 1:i + 25] + ([members[i - 1]] if i > 0 and kind in ("album", "playlist") else [])
        rows = _rows_by_names(con, want)
        after = [rows[n] for n in members[i + 1:i + 25] if n in rows]
        if i > 0 and kind in ("album", "playlist"):
            out["prev"] = rows.get(members[i - 1])
    if len(after) < 24 and kind != "album":
        music = f["section"] == "Music"
        rest = con.execute(
            f"SELECT {_OUT_COLS} FROM files f LEFT JOIN colors k ON k.tv=f.tv WHERE f.dir!=? AND f.name!=? "
            f"AND (f.section='Music')=? ORDER BY f.mtime DESC LIMIT ?",
            (f["collection"], name, 1 if music else 0, 24 - len(after))).fetchall()
        after += [_file_out(x) for x in rest]
    out["next"] = after
    return _cached_json(_json_bytes(out))


@bp.get("/api/random")
def api_random():
    """A random item from a collection (dir=), an artist (artist=) or one of your playlists (list=)."""
    a, con = request.args, _db()
    row = None
    if a.get("dir"):
        row = con.execute("SELECT name FROM files WHERE dir=? ORDER BY RANDOM() LIMIT 1", (a["dir"],)).fetchone()
    elif a.get("artist"):
        pat = "Music/" + a["artist"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"
        row = con.execute("SELECT name FROM files WHERE dir LIKE ? ESCAPE '\\' ORDER BY RANDOM() LIMIT 1", (pat,)).fetchone()
    elif a.get("list"):
        pl = _pl_all().get(a["list"])
        if pl:
            idx = _index()
            names = [n for n in (_pl_resolve(ref, idx) for ref in pl.get("items", [])) if n]
            if names:
                import random
                return jsonify(name=random.choice(names))
    return jsonify(name=row[0] if row else None)


@bp.get("/api/events")
def api_events():
    """Server-sent events: library changes ("lib"), progress made on other devices
    ("progress") and download status ("jobs"). Replaces polling."""
    def stream():
        with _bus:
            last = events._bus_seq
        yield "retry: 3000\n\n"
        yield f"event: lib\ndata: {json.dumps({'v': f'{_BOOT}-{events._lib_version}'})}\n\n"
        yield f"event: jobs\ndata: {_jobs_payload()}\n\n"
        while True:
            with _bus:
                _bus.wait_for(lambda: events._bus_seq > last, timeout=15)
                seq = events._bus_seq
                pending = sorted([(s, k, d) for k, (s, d) in _bus_last.items() if s > last]
                                 + [(s, "progress", d) for s, d in _bus_prog if s > last])
                last = seq
            if not pending:
                yield ": ping\n\n"   # keeps proxies from closing an idle stream
                continue
            for _, kind, data in pending:
                yield f"event: {kind}\ndata: {data}\n\n"
    resp = Response(stream(), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


@bp.get("/api/search")
def api_search():
    """Find words spoken in your videos (full-text index of their captions)."""
    q = re.sub(r"\s+", " ", request.args.get("q") or "").strip()
    terms = re.findall(r"\w+", q)
    if len(q) < 3 or not terms:
        return jsonify(results=[])
    match = '"' + " ".join(terms) + '"' + ("" if q.endswith(" ") else "*")
    con = _db()
    try:
        # one pass over the matches (capped, so a word said everywhere stays quick)
        rows = con.execute("SELECT name, start, text, i FROM captions WHERE captions MATCH ? LIMIT 20000",
                           (match,)).fetchall()
    except sqlite3.OperationalError:
        return jsonify(results=[])
    per: dict[str, list] = {}
    for r in rows:
        per.setdefault(r["name"], []).append(r)
    top = sorted(per, key=lambda n: -len(per[n]))[:30]
    files = _rows_by_names(con, top)
    results = []
    for name in top:
        if name not in files:
            continue
        hits, last = [], -10
        for h in sorted(per[name], key=lambda r: r["i"]):
            if h["i"] > last + 1:   # the next line repeats this one's tail
                hits.append({"t": h["start"], "text": h["text"]})
                if len(hits) == 3:
                    break
            last = h["i"]
        results.append({"name": name, "count": len(per[name]), "hits": hits, "file": files[name]})
    return jsonify(results=results)
