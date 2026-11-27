"""JSON answers with ETags and pre-compressed bodies."""

import hashlib
import json

from flask import Response, request


def _cached_json(body: bytes, etag: str | None = None, gz: bytes | None = None) -> Response:
    """JSON with an ETag (the body's hash unless given): unchanged → 304, no body."""
    etag = etag or hashlib.sha1(body).hexdigest()[:20]
    if request.if_none_match.contains(etag):
        resp = Response(status=304)
    else:
        zipped = "gzip" in (request.headers.get("Accept-Encoding") or "") and len(body) > 1024
        if zipped and gz is None:
            import gzip
            gz = gzip.compress(body, 5)
        resp = Response(gz if zipped else body, mimetype="application/json")
        if zipped:
            resp.headers["Content-Encoding"] = "gzip"
        resp.headers.add("Vary", "Accept-Encoding")
    resp.set_etag(etag)
    resp.headers["Cache-Control"] = "no-cache"
    return resp


def _json_bytes(data) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
