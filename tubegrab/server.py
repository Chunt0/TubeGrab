"""The Flask app: every area's routes, response compression, the built UI, and
the background threads."""

import threading

from flask import Flask, Response, jsonify, request, send_from_directory

from .api import collections, downloads, library, media, playlists, progress
from .config import WEB_DIST
from .index import _indexer
from .jobs import _jobs_publisher
from .maintenance import _startup


app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024   # playlist picture uploads
for _bp in (library, downloads, collections, playlists, media, progress):
    app.register_blueprint(_bp.bp)

_GZIP_TYPES = ("application/json", "text/html", "text/css", "text/javascript", "application/javascript", "image/svg+xml")


@app.after_request
def _changed(resp):
    """Edits to the library are in the index before their answer goes out, so
    whatever the client fetches next already shows them."""
    if (request.method in ("POST", "PATCH", "PUT", "DELETE")
            and not request.path.startswith(("/api/progress", "/api/download", "/api/jobs", "/api/playlists"))):
        _indexer.flush()
    return resp


@app.after_request
def _gzip(resp):
    """Compress text responses: the UI is often used over slow phone links."""
    if (resp.status_code != 200 or resp.mimetype not in _GZIP_TYPES or "Content-Encoding" in resp.headers
            or "gzip" not in (request.headers.get("Accept-Encoding") or "")):
        return resp
    resp.direct_passthrough = False   # files from send_file
    data = resp.get_data()
    if len(data) < 1024:
        return resp
    import gzip
    resp.set_data(gzip.compress(data, 6))
    resp.headers["Content-Encoding"] = "gzip"
    resp.headers.add("Vary", "Accept-Encoding")
    return resp


@app.get("/")
@app.get("/<path:path>")
def spa(path: str = ""):
    """Serve the built React app; unknown paths fall back to index.html."""
    if path.startswith(("api/", "media/", "thumb/", "playlist-cover/", "collection-cover/")):
        return jsonify(error="Not found."), 404
    if not (WEB_DIST / "index.html").exists():
        return Response(
            "UI is not built yet. Run ./run.sh (or `cd web && bun run build`).",
            503, mimetype="text/plain",
        )
    if path and (WEB_DIST / path).is_file():
        return send_from_directory(WEB_DIST, path, max_age=31536000 if path.startswith("assets/") else 0)
    return send_from_directory(WEB_DIST, "index.html", max_age=0)


def start_background() -> None:
    """The indexer, download-status events and startup housekeeping (migrations,
    tidying, backfills). Not started on import, so tests can use the app."""
    threading.Thread(target=_indexer.run, daemon=True, name="indexer").start()
    threading.Thread(target=_jobs_publisher, daemon=True, name="jobs-events").start()
    threading.Thread(target=_startup, daemon=True, name="migrate").start()
