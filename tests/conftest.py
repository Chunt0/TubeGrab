"""Shared setup: the app is imported against a throwaway library folder, with
its background threads off and every network call blocked."""
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

LIB = Path(tempfile.mkdtemp(prefix="tubegrab-test-"))
os.environ["TUBEGRAB_DOWNLOADS"] = str(LIB)
os.environ["TUBEGRAB_NO_THREADS"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as tg  # noqa: E402


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(LIB, ignore_errors=True)


class Offline(Exception):
    pass


@pytest.fixture(autouse=True)
def lib(monkeypatch):
    """An empty library for each test; returns its folder."""
    con = getattr(tg._dbl, "con", None)
    if con is not None:
        con.close()
        tg._dbl.con = None
    for p in LIB.iterdir():
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    for d in (tg.META_DIR, tg.THUMBS_DIR, tg.PL_COVERS_DIR, tg.COVERS_DIR, tg.SMALL_DIR):
        d.mkdir(parents=True, exist_ok=True)
    tg._last_json.clear()
    tg._db_init()
    tg._indexer.known = {}

    def offline(*a, **k):
        raise Offline("network is off in tests")

    monkeypatch.setattr(tg, "_http_json", offline)
    monkeypatch.setattr(tg, "_lrclib", offline)
    monkeypatch.setattr(tg, "_fetch_lyrics_bg", lambda path: None)
    monkeypatch.setattr(tg, "_album_cover", lambda key, album: None)
    monkeypatch.setattr(tg._indexer, "flush", lambda timeout=8: None)
    monkeypatch.setattr(tg, "_mb_last", 0.0)
    return LIB


@pytest.fixture
def client():
    return tg.app.test_client()


def reindex() -> None:
    """What the indexer thread does after a change, done here and now."""
    tg._indexer.walk = tg._indexer.verify = True
    tg._indexer.sync()


def add_file(rel: str, vid: str | None = None, **meta) -> Path:
    """Put an (empty) media file into the library, with an index entry and metadata."""
    path = LIB / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    if vid:
        tg._index_set(vid, tg._kind(path), rel)
        tg._write_meta({"id": vid, **meta})
    return path
