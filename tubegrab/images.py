"""Thumbnails and covers: grid-sized copies, placeholder colours, embedded
album art, square crops, uploaded pictures."""

import hashlib
import os
import subprocess
import threading
import uuid
from pathlib import Path

from flask import request, send_file

from .config import (
    AUDIO_EXTS, COVERS_DIR, DOWNLOAD_DIR, PL_COVERS_DIR, PLACEHOLDER, PLACEHOLDER_AUDIO, SMALL_DIR,
    THUMBS_DIR,
)
from .events import _lib_bump
from .meta import _chosen_cover, _keep_mtime, _meta_thumb, _rel, _video_id
from .files import _generate_thumb
from .db import _db


def _crop_square(path: Path) -> None:
    tmp = path.with_name(path.stem + ".sq" + path.suffix)
    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
             "-vf", "crop='min(iw,ih)':'min(iw,ih)'", "-q:v", "2", "-y", str(tmp)],
            timeout=30, check=True,
        )
        tmp.replace(path)
    except Exception:  # noqa: BLE001 — keep the uncropped cover
        tmp.unlink(missing_ok=True)


_warming = threading.Lock()


def warm_thumbs() -> None:
    """Grid-sized copies + a placeholder colour for every thumbnail, ahead of time."""
    if not _warming.acquire(blocking=False):
        return
    try:
        _warm_thumbs()
    finally:
        _warming.release()


def _warm_thumbs() -> None:
    con = _db()
    todo = con.execute("SELECT f.name, f.tv FROM files f LEFT JOIN colors k ON k.tv=f.tv "
                       "WHERE k.tv IS NULL GROUP BY f.tv").fetchall()
    done = 0
    for r in todo:
        path = DOWNLOAD_DIR / r["name"]
        if not path.is_file():
            continue
        src = _thumb_image(path)
        color = _small(src, color=True, background=True)[1] if src else None
        con.execute("INSERT OR REPLACE INTO colors VALUES(?,?)", (r["tv"], color))
        done += 1
        if done % 200 == 0:
            _lib_bump()
    for p in [*COVERS_DIR.glob("*.jpg"), *PL_COVERS_DIR.glob("*.jpg")]:
        _small(p)
    if done:
        _lib_bump()


def _image_color(img: Path, background: bool = False) -> str | None:
    """Average colour of an image, as #rrggbb (shown while the picture loads)."""
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(img), "-frames:v", "1",
                              "-vf", "scale=1:1:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                             capture_output=True, timeout=15,
                             preexec_fn=(lambda: os.nice(15)) if background else None).stdout
        return "#" + out[:3].hex() if len(out) >= 3 else None
    except Exception:  # noqa: BLE001
        return None


def _placeholder(audio: bool = False):
    return send_file(PLACEHOLDER_AUDIO if audio else PLACEHOLDER, mimetype="image/svg+xml", max_age=3600)


def _embedded_art(path: Path) -> Path | None:
    """Cover art embedded in an audio file (e.g. audio files from elsewhere)."""
    st = path.stat()
    key = hashlib.sha1(f"{path}|{int(st.st_mtime)}|art".encode()).hexdigest()[:16]
    out = THUMBS_DIR / f"{key}.jpg"
    if not out.exists():
        try:
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
                 "-an", "-frames:v", "1", "-y", str(out)],
                timeout=20, check=True,
            )
        except Exception:  # noqa: BLE001 — no embedded art
            out.unlink(missing_ok=True)
            return None
    return out if out.exists() and out.stat().st_size else None


SMALL_W = 480                          # grid cards are ≤ 240 css px wide → sharp on 2× screens
_small_slots = threading.Semaphore(2)  # don't let a fresh grid start 50 ffmpegs on the NAS


def _small(src: Path, color: bool = False, background: bool = False):
    """A ≤ 480 px wide JPEG copy of `src` (made once, cached); `src` if it already is
    small. With `color`: (copy, average colour "#rrggbb") — one ffmpeg run for both."""
    def done(path: Path, rgb: bytes | None = None):
        if not color:
            return path
        return path, ("#" + rgb[:3].hex() if rgb and len(rgb) >= 3 else _image_color(path, background))
    try:
        st = src.stat()
    except OSError:
        return done(src, b"") if color else src
    if st.st_size < 30_000:
        return done(src)
    out = SMALL_DIR / (hashlib.sha1(f"{src}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()[:20] + ".jpg")
    if out.exists():
        return done(out)
    with _small_slots:
        if out.exists():
            return done(out)
        tmp = SMALL_DIR / f".{uuid.uuid4().hex}.jpg"
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(src), "-frames:v", "1",
               "-vf", f"scale='min({SMALL_W},iw)':-2", "-q:v", "5", "-y", str(tmp)]
        if color:   # second output: the picture shrunk to one averaged pixel
            cmd += ["-frames:v", "1", "-vf", "scale=1:1:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
        try:
            r = subprocess.run(cmd, timeout=20, check=True, capture_output=True,
                               preexec_fn=(lambda: os.nice(15)) if background else None)
            if not tmp.stat().st_size:
                raise OSError("empty")
            tmp.replace(out)
        except Exception:  # noqa: BLE001 — serve the original
            tmp.unlink(missing_ok=True)
            return done(src)
    return done(out, r.stdout if color else None)


def _send_image(src: Path, max_age: int = 86400, mimetype: str | None = None):
    """An image, grid-sized unless ?full=1. Versioned URLs (?v=…) never change, so
    browsers keep them for a year without asking again."""
    out = src if request.args.get("full") else _small(src)
    resp = send_file(out, mimetype="image/jpeg" if out != src else mimetype, max_age=max_age)
    if max_age >= 31536000:
        resp.cache_control.immutable = True
    return resp


def _thumb_image(path: Path, own: bool = False) -> Path | None:
    """The picture behind a file's thumbnail: the album cover you chose, YouTube's
    thumbnail, embedded art, or a frame from the video."""
    chosen = None if own else _chosen_cover(path)
    yt = chosen[0] if chosen else _meta_thumb(_video_id(path))
    if yt:
        return yt
    if path.suffix.lower() in AUDIO_EXTS:
        return _embedded_art(path)
    st = path.stat()
    out = THUMBS_DIR / (hashlib.sha1(f"{_rel(path)}|{int(st.st_mtime)}".encode()).hexdigest()[:16] + ".jpg")
    if not out.exists():
        _generate_thumb(path, out)
    return out if out.exists() else None


def _embed_cover(path: Path, jpg: Path) -> None:
    """Replace the embedded cover of an .m4a with `jpg`."""
    if path.suffix.lower() != ".m4a" or not jpg.exists():
        return
    try:
        from mutagen.mp4 import MP4, MP4Cover
        restore = _keep_mtime(path)
        f = MP4(str(path))
        f["covr"] = [MP4Cover(jpg.read_bytes(), imageformat=MP4Cover.FORMAT_JPEG)]
        f.save()
        restore()
    except Exception as exc:  # noqa: BLE001
        print(f"[tubegrab] cover embed failed for {path.name}: {exc}")


def _image_demuxer(head: bytes) -> str | None:
    """The ffmpeg demuxer for an uploaded picture, from its first bytes; None if it
    isn't a JPEG, PNG, WebP, GIF or BMP. Uploads come from anyone who can reach the
    site, and left to guess the format ffmpeg also opens playlists that read other
    files (or URLs), so only these image formats are ever handed to it."""
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg_pipe"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png_pipe"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp_pipe"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if head.startswith(b"BM"):
        return "bmp_pipe"
    return None


def _save_picture(up, dest: Path, wide: bool = False) -> bool:
    """Uploaded image → cropped JPEG at `dest` (square, or 16:9 when `wide`)."""
    crop = ("crop='min(iw,ih*16/9)':'min(ih,iw*9/16)',scale='min(1600,iw)':-2" if wide
            else "crop='min(iw,ih)':'min(iw,ih)',scale='min(1000,iw)':-2")
    tmp = dest.with_name(f".{uuid.uuid4().hex}.upload")
    out = dest.with_name(f".{uuid.uuid4().hex}.jpg")
    try:
        up.save(tmp)
        with tmp.open("rb") as fh:
            demuxer = _image_demuxer(fh.read(16))
        if demuxer is None:
            return False
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file",
                        "-f", demuxer, "-i", str(tmp), "-frames:v", "1",
                        "-vf", crop, "-q:v", "3", "-y", str(out)], timeout=30, check=True)
        if not out.exists() or out.stat().st_size == 0:
            return False
        out.replace(dest)
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        tmp.unlink(missing_ok=True)
        out.unlink(missing_ok=True)


BAD_PICTURE = "That file couldn't be read as an image (try a JPEG or PNG)."
