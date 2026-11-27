"""The indexer: one background thread keeps the SQLite library index (see db)
in step with the files and .meta, and builds each row and collection."""

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from .config import (
    AUDIO_EXTS, COVERS_DIR, DOWNLOAD_DIR, FOLDERS_FILE, ID_RE, INDEX_FILE, INDEX_PREFIX_RE, MEDIA_EXTS,
    META_DIR, SUB_LANGS,
)
from .events import _lib_bump
from .meta import _folder_cover, _index, _iter_media, _kind, _meta_for, _meta_thumb, _read_json, _TEMP_STEM
from .naming import _clean_channel
from .files import _clean_chapters, _collection_info, _display_title, _probe
from .db import _db, _db_init, _FILE_COLS, _INSERT_FILE, _search_text, _tx
from .transcripts import _parse_vtt, _sub_file


class _Indexer:
    def __init__(self):
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.vids: set[str] = set()       # metadata of these ids changed
        self.names: set[str] = set()      # these rows need rebuilding
        self.dirs: set[str] = set()       # every row in these folders needs rebuilding
        self.walk = True                  # look for changed folders
        self.verify = True                # compare every row's metadata stamp (startup, hourly)
        self.ready = threading.Event()    # first full pass done
        self.known: dict[str, int] = {}   # folder → mtime_ns, mirrors the dirs table
        self.asked = 0                    # kicks so far / kicks a finished pass has covered
        self.covered = 0
        self.urgent = False
        self.done = threading.Condition()

    def kick(self, *, walk: bool = True, vids=(), names=(), dirs=()) -> int:
        with self.lock:
            self.walk = self.walk or walk
            self.vids.update(v for v in vids if v)
            self.names.update(n for n in names if n)
            self.dirs.update(d for d in dirs if d is not None)
            self.asked += 1
            n = self.asked
        self.wake.set()
        return n

    def flush(self, timeout: float = 8) -> None:
        """Apply pending changes now and wait for them (after an edit, so the
        answer the client gets next already shows it)."""
        if threading.current_thread().name == "indexer":
            return
        self.urgent = True               # (before the kick, so the indexer doesn't pause first)
        n = self.kick()
        with self.done:
            self.done.wait_for(lambda: self.covered >= n, timeout=timeout)

    def run(self) -> None:
        _db_init()
        self.known = {r[0]: r[1] for r in _db().execute("SELECT path, mtime FROM dirs")}
        last_verify = time.monotonic()
        while True:
            with self.lock:
                covers = self.asked
            try:
                self.sync()
            except Exception as exc:  # noqa: BLE001 — keep indexing
                import traceback
                traceback.print_exc()
                print(f"[tubegrab] index sync failed: {exc}")
            self.ready.set()
            with self.done:
                self.covered = covers
                self.done.notify_all()
            self.wake.wait(timeout=60)    # no news: look for outside changes (SMB, File Station) once a minute
            if not self.wake.is_set():
                with self.lock:
                    self.walk = True
                    if time.monotonic() - last_verify > 3600:
                        self.verify, last_verify = True, time.monotonic()
            self.wake.clear()
            for _ in range(6):            # let bursts of changes (a download landing) settle into one pass…
                if self.urgent:           # …unless an edit is waiting for it
                    break
                time.sleep(0.05)
            self.urgent = False

    def sync(self) -> None:
        with self.lock:
            walk, verify = self.walk, self.verify
            vids, names, force_dirs = self.vids, self.names, self.dirs
            self.walk = self.verify = False
            self.vids, self.names, self.dirs = set(), set(), set()
        con = _db()
        changed_dirs: set[str] = set(force_dirs)
        gone_dirs: set[str] = set()
        if walk:
            seen: dict[str, int] = {}
            base = len(str(DOWNLOAD_DIR)) + 1
            stack = [str(DOWNLOAD_DIR)]
            while stack:
                d = stack.pop()
                try:
                    mt = os.stat(d).st_mtime_ns
                    with os.scandir(d) as it:
                        stack.extend(e.path for e in it if not e.name.startswith(".") and e.is_dir(follow_symlinks=False))
                except OSError:
                    continue
                rel = d[base:].replace(os.sep, "/") if len(d) >= base else ""
                seen[rel] = mt
                if self.known.get(rel) != mt:
                    changed_dirs.add(rel)
            gone_dirs = set(self.known) - set(seen)
            new_known = seen
        else:
            new_known = None

        folders = _json_cached(FOLDERS_FILE)
        by_path = {v: k.split(":")[0] for k, v in _json_cached(INDEX_FILE).items()}
        covers: dict = {}
        upserts: list[dict] = []
        deletes: set[str] = set()
        touched: set[str] = set(gone_dirs)

        for d in gone_dirs:
            for (n,) in con.execute("SELECT name FROM files WHERE dir=?", (d,)):
                deletes.add(n)
        for d in changed_dirs:
            if d in gone_dirs:
                continue
            full = os.path.join(DOWNLOAD_DIR, d) if d else str(DOWNLOAD_DIR)
            have = {r["name"]: r for r in con.execute("SELECT * FROM files WHERE dir=?", (d,))}
            present = set()
            try:
                entries = list(os.scandir(full))
            except OSError:
                entries = []
            for e in entries:
                n = e.name
                stem, ext = os.path.splitext(n)
                if n.startswith(".") or ext.lower() not in MEDIA_EXTS or _TEMP_STEM.search(stem):
                    continue
                try:
                    if not e.is_file():
                        continue
                    st = e.stat()
                except OSError:
                    continue
                rel = f"{d}/{n}" if d else n
                present.add(rel)
                old = have.get(rel)
                if old is None or d in force_dirs or rel in names or old["fstamp"] != f"{st.st_mtime_ns}:{st.st_size}":
                    upserts.append(_build_row(e.path, rel, st, by_path, folders, covers, old))
            for rel in set(have) - present:
                deletes.add(rel)
            touched.add(d)

        # metadata changes (and periodic verification) for rows not rebuilt above
        done = {u["name"] for u in upserts} | deletes
        redo: set[str] = set(n for n in names if n not in done)
        if vids:
            vl = list(vids)
            for i in range(0, len(vl), 500):
                chunk = vl[i:i + 500]
                redo.update(r[0] for r in con.execute(
                    f"SELECT name FROM files WHERE vid IN ({','.join('?' * len(chunk))})", chunk))
        if verify:
            listing = set(os.listdir(META_DIR))
            for r in con.execute("SELECT name, vid, mstamp FROM files"):
                if r["name"] in done or not r["vid"]:
                    continue
                if _meta_stamp(r["vid"], listing) != r["mstamp"]:
                    redo.add(r["name"])
        for rel in redo - done:
            full = os.path.join(DOWNLOAD_DIR, rel)
            try:
                st = os.stat(full)
            except OSError:
                deletes.add(rel)
                continue
            old = con.execute("SELECT * FROM files WHERE name=?", (rel,)).fetchone()
            upserts.append(_build_row(full, rel, st, by_path, folders, covers, old))

        for u in upserts:
            touched.add(u["dir"])
        for n in deletes:
            touched.add(n.rpartition("/")[0])
        dirs_moved = new_known is not None and new_known != self.known
        if not upserts and not deletes and not dirs_moved and not force_dirs:
            return
        coll_changed = False

        with _tx(con):
            if deletes:
                dl = list(deletes)
                for i in range(0, len(dl), 500):
                    chunk = dl[i:i + 500]
                    q = ",".join("?" * len(chunk))
                    con.execute(f"DELETE FROM files WHERE name IN ({q})", chunk)
                    for lo, hi in con.execute(f"SELECT lo, hi FROM caption_state WHERE name IN ({q}) AND lo IS NOT NULL", chunk).fetchall():
                        con.execute("DELETE FROM captions WHERE rowid BETWEEN ? AND ?", (lo, hi))
                    con.execute(f"DELETE FROM caption_state WHERE name IN ({q})", chunk)
            con.executemany(_INSERT_FILE, [tuple(u[c] for c in _FILE_COLS) for u in upserts])
            for key in touched:
                if not key:
                    continue
                data = _collection_data(con, key, folders)
                old = con.execute("SELECT data FROM collections WHERE key=?", (key,)).fetchone()
                if data is None:
                    if old:
                        con.execute("DELETE FROM collections WHERE key=?", (key,))
                        coll_changed = True
                    continue
                blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
                if not old or old[0] != blob:
                    con.execute("INSERT OR REPLACE INTO collections VALUES(?,?,?,?)", (key, data["section"], data["updated"], blob))
                    coll_changed = True
            if new_known is not None:
                for d in gone_dirs:
                    con.execute("DELETE FROM dirs WHERE path=?", (d,))
                con.executemany("INSERT OR REPLACE INTO dirs VALUES(?,?)",
                                [(d, m) for d, m in new_known.items() if self.known.get(d) != m])
        if new_known is not None:
            self.known = new_known
        videos = [u for u in upserts if u["kind"] == "video"]
        for i in range(0, len(videos), 40):   # short transactions: progress writes never wait long
            with _tx(con):
                for u in videos[i:i + 40]:
                    _index_captions(con, u["name"], u["vid"] if u["subs"] else None)
        if upserts or deletes or coll_changed:
            _lib_bump()


_indexer = _Indexer()


def _find_existing(vid: str, kind: str) -> Path | None:
    """The library file of this video (`kind`: audio/video), if it is already there."""
    rel = _index().get(f"{vid}:{kind}")
    if rel and (DOWNLOAD_DIR / rel).is_file():
        return DOWNLOAD_DIR / rel
    if _indexer.ready.is_set():
        # legacy "Title [id].ext" names: the index read their ids from the names
        for (name,) in _db().execute("SELECT name FROM files WHERE vid=? AND kind=?", (vid, kind)):
            if (DOWNLOAD_DIR / name).is_file():
                return DOWNLOAD_DIR / name
        return None
    for path in _iter_media():   # (before the first index pass: look through the folders)
        m = ID_RE.search(path.stem)
        if m and m.group(1) == vid and _kind(path) == kind:
            return path
    return None
_jc: dict[str, tuple[int, dict]] = {}


def _json_cached(path: Path) -> dict:
    """index.json / folders.json for the indexer: parsed again only when they change."""
    try:
        mt = path.stat().st_mtime_ns
    except OSError:
        return {}
    hit = _jc.get(str(path))
    if hit and hit[0] == mt:
        return hit[1]
    data = _read_json(path, {})
    _jc[str(path)] = (mt, data)
    return data


def _meta_stamp(vid: str | None, listing: set[str] | None = None) -> str:
    """Changes when anything a row shows from .meta changes: the metadata file, and
    whether captions / lyrics exist."""
    if not vid:
        return ""
    try:
        st = os.stat(f"{META_DIR}/{vid}.json")
        j = f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        j = ""
    if listing is not None:
        has = lambda s: f"{vid}{s}" in listing   # noqa: E731
    else:
        has = lambda s: os.path.exists(f"{META_DIR}/{vid}{s}")   # noqa: E731
    flags = "".join("1" if has(sfx) else "0" for sfx in (".lrc", ".lyrics.txt", *(f".{lang}.vtt" for lang in SUB_LANGS)))
    return f"{j}|{flags}"


def _build_row(full: str, rel: str, st: os.stat_result, by_path: dict, folders: dict, covers: dict,
               old: sqlite3.Row | None) -> dict:
    fname = rel.rpartition("/")[2]
    stem, ext = os.path.splitext(fname)
    kind = "audio" if ext.lower() in AUDIO_EXTS else "video"
    m = ID_RE.search(stem)
    vid = by_path.get(rel) or (m.group(1) if m else None)
    meta = _meta_for(vid)
    fstamp = f"{st.st_mtime_ns}:{st.st_size}"
    if old is not None and old["fstamp"] == fstamp and old["duration"] is not None:
        duration, height = old["duration"], old["height"]   # same file: no need to ffprobe again
    else:
        probe = _probe(full, st)
        duration, height = probe["duration"] or meta.get("duration"), probe["height"]
    key = rel.rpartition("/")[0]
    c = _collection_info(key) if key else {"section": "Other", "artist": None}
    art = None
    src = None
    if c["section"] == "Music" and kind == "audio" and key:
        if key not in covers:
            covers[key] = _folder_cover(key, folders)
        if covers[key]:
            src, art = covers[key]
    if src is None and vid:
        src = _meta_thumb(vid)
    try:
        s2 = src.stat() if src else None
        basis = f"{src}|{s2.st_mtime_ns}|{s2.st_size}" if s2 else f"{rel}|{fstamp}"
    except OSError:
        basis = f"{rel}|{fstamp}"
    title = meta.get("track") or meta.get("title") or _display_title(fname)
    artist = meta.get("artist") or c.get("artist") or _clean_channel(meta.get("uploader"))
    uploader = _clean_channel(meta.get("uploader") or meta.get("channel"))
    album = meta.get("album")
    mi = INDEX_PREFIX_RE.match(fname)
    ch = _clean_chapters(meta)
    lyrics = None
    if vid and kind == "audio":
        lyrics = ("synced" if os.path.exists(f"{META_DIR}/{vid}.lrc")
                  else "plain" if os.path.exists(f"{META_DIR}/{vid}.lyrics.txt") else None)
    return {
        "name": rel, "dir": key, "section": c["section"], "kind": kind,
        "title": title, "sort_title": title.casefold(),
        "search": _search_text(title, artist, album, uploader, key),
        "size": st.st_size, "mtime": st.st_mtime, "duration": duration,
        "height": height if kind == "video" else None,
        "vid": vid, "ref": f"{vid}:{kind}" if vid else f"path:{rel}",
        "artist": artist, "album": album, "uploader": uploader, "upload_date": meta.get("upload_date"),
        "year": meta.get("release_year"), "url": meta.get("webpage_url"),
        "idx": int(mi.group(1)) if mi else None, "art": art,
        "tv": hashlib.sha1(basis.encode()).hexdigest()[:10],
        "chapters": json.dumps(ch) if ch else None,
        "subs": 1 if vid and _sub_file(vid) else 0, "lyrics": lyrics,
        "fstamp": fstamp, "mstamp": _meta_stamp(vid),
    }


def _collection_data(con: sqlite3.Connection, key: str, folders: dict) -> dict | None:
    rows = [dict(r) for r in con.execute(
        "SELECT name, title, idx, size, duration, mtime, vid, album, tv FROM files WHERE dir=?", (key,))]
    if not rows:
        return None
    c = _collection_info(key)
    extra = folders.get(key, {})
    named = bool(extra.get("title")) and c["kind"] != "singles"
    if named:
        c["title"] = extra["title"]   # YouTube's original name, or the user's edit
    if c["kind"] in ("album", "playlist"):
        rows.sort(key=lambda f: (f["idx"] is None, f["idx"] or 0, f["title"].lower()))
    else:
        rows.sort(key=lambda f: -f["mtime"])
    c.update(url=extra.get("url"), count=len(rows), size=sum(f["size"] for f in rows),
             duration=sum(f["duration"] or 0 for f in rows), updated=max(f["mtime"] for f in rows),
             first=rows[0]["name"], art=None, cover_url=None, genres=extra.get("genres") or [])
    shown = rows
    custom = _folder_cover(key, folders)
    if custom:
        c["art"] = custom[1]
        if custom[0].parent == COVERS_DIR:
            c["cover_url"] = f"/collection-cover/{custom[1]}"
        else:   # an item's thumbnail: put that item first
            shown = sorted(rows, key=lambda f: f["vid"] != custom[1])
    c["covers"] = [{"name": f["name"], "tv": f["tv"]} for f in shown[:3]]
    if c["kind"] == "album" and not named:
        # real album name from tags ("Global Entry: Deluxe"), not the filename-safe folder
        albums = [f["album"] for f in rows if f.get("album")]
        if albums:
            c["title"] = max(set(albums), key=albums.count)
    return c


def _index_captions(con: sqlite3.Connection, name: str, vid: str | None) -> None:
    """(Re)index a video's captions for search, if they changed. Runs inside the
    caller's transaction. A video's lines are stored together, so its old ones go
    by rowid range (the text index can't look rows up by name)."""
    sub = _sub_file(vid) if vid else None
    stamp = ""
    if sub:
        st = sub.stat()
        stamp = f"{sub.name}|{st.st_mtime_ns}|{st.st_size}"
    row = con.execute("SELECT stamp, lo, hi FROM caption_state WHERE name=?", (name,)).fetchone()
    if row is not None and row["stamp"] == stamp:
        return
    if row is None and not stamp:
        con.execute("INSERT OR REPLACE INTO caption_state VALUES(?,?,NULL,NULL)", (name, ""))
        return
    if row is not None and row["lo"] is not None:
        con.execute("DELETE FROM captions WHERE rowid BETWEEN ? AND ?", (row["lo"], row["hi"]))
    segs = []
    if sub:
        try:
            segs = _parse_vtt(sub.read_text("utf-8", errors="replace"))
        except Exception:  # noqa: BLE001
            segs = []
    rows = []
    for k, sg in enumerate(segs):
        # a line plus the start of the next, so phrases split across caption lines still match
        nxt = " ".join(segs[k + 1]["text"].split()[:8]) if k + 1 < len(segs) else ""
        rows.append(((sg["text"] + " " + nxt).strip(), name, sg["start"], k))
    lo = hi = None
    if rows:
        top = con.execute("SELECT COALESCE(MAX(rowid), 0) FROM captions").fetchone()[0]
        lo, hi = top + 1, top + len(rows)
        con.executemany("INSERT INTO captions(rowid, text, name, start, i) VALUES(?,?,?,?,?)",
                        [(lo + n, *r) for n, r in enumerate(rows)])
    con.execute("INSERT OR REPLACE INTO caption_state VALUES(?,?,?,?)", (name, stamp, lo, hi))
