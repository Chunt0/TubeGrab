"""Serving files: media (with ranges), thumbnails, lyrics and transcripts."""

import mimetypes
import re

from flask import Blueprint, Response, jsonify, request
from werkzeug.wsgi import wrap_file

from ..config import AUDIO_EXTS, META_DIR
from ..meta import _video_id
from ..files import _safe_path
from ..images import _placeholder, _send_image, _thumb_image
from ..transcripts import _transcript
from ..lyrics import fetch_lyrics, _parse_lrc


bp = Blueprint("media", __name__)


@bp.get("/api/transcript/<path:name>")
def api_transcript(name: str):
    path = _safe_path(name)
    if path is None:
        return jsonify(error="File not found."), 404
    return jsonify(segments=_transcript(_video_id(path)))


@bp.get("/api/lyrics/<path:name>")
def api_lyrics(name: str):
    path = _safe_path(name)
    if path is None:
        return jsonify(error="File not found."), 404
    vid = _video_id(path)
    lrc, txt = META_DIR / f"{vid}.lrc", META_DIR / f"{vid}.lyrics.txt"
    if vid and lrc.exists():
        return jsonify(synced=_parse_lrc(lrc.read_text("utf-8", errors="replace")), plain=None)
    if vid and txt.exists():
        return jsonify(synced=None, plain=txt.read_text("utf-8", errors="replace"))
    return jsonify(synced=None, plain=None)


@bp.post("/api/lyrics/<path:name>")
def api_lyrics_refresh(name: str):
    """Look again (e.g. after fixing a song's title)."""
    path = _safe_path(name)
    if path is None:
        return jsonify(error="File not found."), 404
    try:
        kind = fetch_lyrics(path, force=True)
    except Exception:  # noqa: BLE001
        return jsonify(error="Couldn't reach the lyrics service."), 502
    return jsonify(found=kind)



def _file_body(f, length: int):
    """`length` bytes of an open file from its current position. Under gunicorn the
    kernel copies them straight to the socket (sendfile, bounded by Content-Length);
    elsewhere they are streamed in chunks. Either way nothing big sits in memory."""
    if request.environ.get("SERVER_SOFTWARE", "").startswith("gunicorn"):
        return wrap_file(request.environ, f, buffer_size=256 * 1024)

    def chunks():
        left = length
        try:
            while left > 0:
                data = f.read(min(256 * 1024, left))
                if not data:
                    break
                left -= len(data)
                yield data
        finally:
            f.close()
    return chunks()


@bp.get("/media/<path:name>")
def media(name: str):
    """Stream a library file with HTTP Range support so seeking works."""
    path = _safe_path(name)
    if path is None:
        return jsonify(error="File not found."), 404
    size = path.stat().st_size
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if path.suffix.lower() == ".m4a":
        mime = "audio/mp4"

    m = re.match(r"^bytes=(\d*)-(\d*)$", request.headers.get("Range", "").strip())
    start, end, status = 0, size - 1, 200
    if m and (m.group(1) or m.group(2)):
        if m.group(1):                      # bytes=start-  /  bytes=start-end
            start = int(m.group(1))
            end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
        else:                               # suffix range: bytes=-N (last N bytes)
            start = max(size - int(m.group(2)), 0)
        if start >= size or start > end:
            resp = Response(status=416)
            resp.headers["Content-Range"] = f"bytes */{size}"
            return resp
        status = 206
    length = end - start + 1
    f = path.open("rb")
    f.seek(start)
    resp = Response(_file_body(f, length), status, mimetype=mime, direct_passthrough=True)
    resp.headers["Content-Length"] = str(length)
    if status == 206:
        resp.headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Accept-Ranges"] = "bytes"
    resp.headers["Content-Disposition"] = "inline"
    return resp


@bp.get("/thumb/<path:name>")
def thumb(name: str):
    """Thumbnail: YouTube's own if we have it, else embedded art / a poster frame."""
    path = _safe_path(name)
    if path is None:
        return jsonify(error="File not found."), 404
    src = _thumb_image(path, own=bool(request.args.get("own")))
    if src is None:
        return _placeholder(audio=path.suffix.lower() in AUDIO_EXTS)
    return _send_image(src, 31536000 if request.args.get("v") else 86400)
