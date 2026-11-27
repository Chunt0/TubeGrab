"""The library is indexed in SQLite (.meta/library.db): one row per media file,
one per collection, plus a full-text index of video captions. It is kept in
step incrementally: folder mtimes say which folders changed (files added,
removed, renamed or replaced), and metadata writes made by this process mark
exactly the rows they affect. It is only an index — delete it and it is
rebuilt from the files."""

import json
import sqlite3
import threading

from .config import META_DIR
from .meta import _progress



DB_FILE = META_DIR / "library.db"
DB_SCHEMA = 2
_dbl = threading.local()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files(
  name TEXT PRIMARY KEY, dir TEXT NOT NULL, section TEXT NOT NULL, kind TEXT NOT NULL,
  title TEXT NOT NULL, sort_title TEXT NOT NULL, search TEXT NOT NULL,
  size INTEGER NOT NULL, mtime REAL NOT NULL, duration REAL, height INTEGER,
  vid TEXT, ref TEXT NOT NULL, artist TEXT, album TEXT, uploader TEXT, upload_date TEXT,
  year TEXT, url TEXT, idx INTEGER, art TEXT, tv TEXT NOT NULL, chapters TEXT,
  subs INTEGER NOT NULL DEFAULT 0, lyrics TEXT, fstamp TEXT NOT NULL, mstamp TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS files_dir ON files(dir);
CREATE INDEX IF NOT EXISTS files_section ON files(section, mtime);
CREATE INDEX IF NOT EXISTS files_ref ON files(ref);
CREATE INDEX IF NOT EXISTS files_vid ON files(vid);
CREATE INDEX IF NOT EXISTS files_mtime ON files(mtime);
CREATE TABLE IF NOT EXISTS collections(key TEXT PRIMARY KEY, section TEXT NOT NULL, updated REAL NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dirs(path TEXT PRIMARY KEY, mtime INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS progress(ref TEXT PRIMARY KEY, pos REAL NOT NULL DEFAULT 0, watched INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS colors(tv TEXT PRIMARY KEY, color TEXT);
CREATE TABLE IF NOT EXISTS caption_state(name TEXT PRIMARY KEY, stamp TEXT NOT NULL, lo INTEGER, hi INTEGER);
CREATE VIRTUAL TABLE IF NOT EXISTS captions USING fts5(text, name UNINDEXED, start UNINDEXED, i UNINDEXED,
  tokenize='porter unicode61 remove_diacritics 2');
"""
_FILE_COLS = ("name", "dir", "section", "kind", "title", "sort_title", "search", "size", "mtime", "duration",
              "height", "vid", "ref", "artist", "album", "uploader", "upload_date", "year", "url", "idx", "art",
              "tv", "chapters", "subs", "lyrics", "fstamp", "mstamp")
_INSERT_FILE = f"INSERT OR REPLACE INTO files({','.join(_FILE_COLS)}) VALUES({','.join('?' * len(_FILE_COLS))})"


def _db() -> sqlite3.Connection:
    """This thread's connection (WAL: readers never wait for the indexer)."""
    con = getattr(_dbl, "con", None)
    if con is None:
        con = sqlite3.connect(DB_FILE, timeout=15, isolation_level=None, check_same_thread=False)
        con.row_factory = sqlite3.Row
        for p in ("journal_mode=WAL", "synchronous=NORMAL", "busy_timeout=15000", "temp_store=MEMORY", "cache_size=-16000"):
            con.execute(f"PRAGMA {p}")
        _dbl.con = con
    return con


class _tx:
    """BEGIN IMMEDIATE … COMMIT (or ROLLBACK on error)."""
    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def __enter__(self):
        self.con.execute("BEGIN IMMEDIATE")
        return self.con

    def __exit__(self, exc_type, *_):
        self.con.execute("ROLLBACK" if exc_type else "COMMIT")
        return False


def _db_init() -> None:
    con = _db()
    if con.execute("PRAGMA user_version").fetchone()[0] != DB_SCHEMA:
        for t in ("files", "collections", "dirs", "progress", "colors", "caption_state", "captions"):
            con.execute(f"DROP TABLE IF EXISTS {t}")
        con.execute("VACUUM")
    con.executescript(_SCHEMA)
    con.execute(f"PRAGMA user_version={DB_SCHEMA}")
    _progress_to_db(_progress())


def _progress_to_db(prog: dict) -> None:
    """progress.json is the record; the table mirrors it so queries can join it."""
    con = _db()
    with _tx(con):
        con.execute("DELETE FROM progress")
        con.executemany("INSERT INTO progress VALUES(?,?,?)",
                        [(r, float(p.get("pos") or 0), 1 if p.get("watched") else 0) for r, p in prog.items()])


# -- reading the index

_OUT_COLS = ("f.name, f.dir, f.section, f.kind, f.title, f.size, f.mtime, f.duration, f.height, f.vid, f.artist, "
             "f.album, f.uploader, f.upload_date, f.year, f.url, f.idx, f.art, f.tv, f.subs, f.lyrics, k.color")


def _file_out(r: sqlite3.Row, chapters: str | None = None) -> dict:
    out = {
        "name": r["name"], "title": r["title"], "kind": r["kind"], "size": r["size"], "mtime": r["mtime"],
        "duration": r["duration"], "height": r["height"], "section": r["section"], "collection": r["dir"],
        "id": r["vid"], "artist": r["artist"], "album": r["album"], "uploader": r["uploader"],
        "upload_date": r["upload_date"], "year": r["year"], "url": r["url"], "index": r["idx"],
        "art": r["art"], "tv": r["tv"], "color": r["color"], "subs": bool(r["subs"]), "lyrics": r["lyrics"],
    }
    if chapters is not None:
        out["chapters"] = json.loads(chapters) if chapters else None
    return out


def _rows_by_names(con: sqlite3.Connection, names: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(names), 500):
        chunk = names[i:i + 500]
        for r in con.execute(f"SELECT {_OUT_COLS} FROM files f LEFT JOIN colors k ON k.tv=f.tv "
                             f"WHERE f.name IN ({','.join('?' * len(chunk))})", chunk):
            out[r["name"]] = _file_out(r)
    return out


def _order_sql(kind: str) -> str:
    return ("f.idx IS NULL, f.idx, f.sort_title" if kind in ("album", "playlist") else "f.mtime DESC")
