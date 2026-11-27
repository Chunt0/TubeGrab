"""Download routes: start, list and cancel jobs."""

import threading
import uuid

from flask import Blueprint, jsonify, request

from ..config import DEFAULT_FORMAT, FORMAT_ALIASES, FORMATS, _jobs, _lock, _now
from ..events import _jobs_dirty
from ..jobs import _forget_old_jobs, _run_job, serialize


bp = Blueprint("downloads", __name__)


@bp.post("/api/download")
def api_download():
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()
    fmt = data.get("format") or DEFAULT_FORMAT
    fmt = FORMAT_ALIASES.get(fmt, fmt)
    playlist = bool(data.get("playlist", True))

    if not url:
        return jsonify(error="A URL is required."), 400
    if not url.lower().startswith(("http://", "https://")):
        return jsonify(error="URL must start with http:// or https://"), 400
    if fmt not in FORMATS:
        return jsonify(error="Unknown format."), 400

    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id, "url": url, "format": fmt, "playlist": playlist,
        "status": "queued", "title": None, "error": None,
        "percent": None, "speed": None, "eta": None,
        "downloaded": 0, "total": None, "current_file": None,
        "playlist_index": None, "playlist_count": None, "entry_count": None,
        "files": [], "created_at": _now(), "finished_at": None,
        "cancel": False,
    }
    _forget_old_jobs()
    with _lock:
        _jobs[job_id] = job
    _jobs_dirty.set()
    threading.Thread(target=_run_job, args=(job_id, url, fmt, playlist), daemon=True).start()
    return jsonify(job=serialize(job))


@bp.get("/api/jobs")
def api_jobs():
    with _lock:
        jobs = [serialize(j) for j in _jobs.values()]
    jobs.sort(key=lambda j: j["created_at"], reverse=True)
    return jsonify(jobs=jobs)


@bp.post("/api/jobs/<job_id>/cancel")
def api_cancel(job_id: str):
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return jsonify(error="Unknown job."), 404
        if job["status"] in ("queued", "downloading"):
            job["cancel"] = True
    return jsonify(ok=True)
