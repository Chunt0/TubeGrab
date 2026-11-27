"""Shared setup: the app is imported against a throwaway library folder (its
background threads only start from app.py) with every network call blocked."""
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

LIB = Path(tempfile.mkdtemp(prefix="tubegrab-test-"))
os.environ["TUBEGRAB_DOWNLOADS"] = str(LIB)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tubegrab import albums, config, db, index, meta, server  # noqa: E402


def patch(monkeypatch, name: str, value) -> None:
    """Replace `name` in every tubegrab module that has it (they import each other's functions)."""
    for mod in [m for k, m in sys.modules.items() if k == "tubegrab" or k.startswith("tubegrab.")]:
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, value)


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(LIB, ignore_errors=True)


class Offline(Exception):
    pass


@pytest.fixture(autouse=True)
def lib(monkeypatch):
    """An empty library for each test; returns its folder."""
    con = getattr(db._dbl, "con", None)
    if con is not None:
        con.close()
        db._dbl.con = None
    for p in LIB.iterdir():
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    for d in (config.META_DIR, config.THUMBS_DIR, config.PL_COVERS_DIR, config.COVERS_DIR, config.SMALL_DIR):
        d.mkdir(parents=True, exist_ok=True)
    meta._last_json.clear()
    db._db_init()
    index._indexer.known = {}
    index._indexer.ready.clear()
    config._jobs.clear()

    def offline(*a, **k):
        raise Offline("network is off in tests")

    patch(monkeypatch, "_http_json", offline)
    patch(monkeypatch, "_lrclib", offline)
    patch(monkeypatch, "_fetch_lyrics_bg", lambda path: None)
    patch(monkeypatch, "_album_cover", lambda key, album: None)
    monkeypatch.setattr(index._indexer, "flush", lambda timeout=8: None)
    monkeypatch.setattr(albums, "_mb_last", 0.0)
    return LIB


@pytest.fixture
def client():
    return server.app.test_client()


def reindex() -> None:
    """What the indexer thread does after a change, done here and now."""
    index._indexer.walk = index._indexer.verify = True
    index._indexer.sync()


def add_file(rel: str, vid: str | None = None, **info) -> Path:
    """Put an (empty) media file into the library, with an index entry and metadata."""
    path = LIB / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    if vid:
        meta._index_set(vid, meta._kind(path), rel)
        meta._write_meta({"id": vid, **info})
    return path
