"""Downloads: the queue, yt-dlp options and post-processors, filing each
entry where _plan says, and download status for the UI."""

import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

import yt_dlp
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.postprocessor.sponsorblock import SponsorBlockPP

from .config import (
    COOKIES_FILE, DOWNLOAD_DIR, FORMATS, INDEX_PREFIX_RE, _jobs, JS_RUNTIMES, _lock, MEDIA_EXTS, META_DIR,
    _meta_lock, _now, PLAYLISTS_FILE, SECTIONS, SUB_OPTS,
)
from .events import _jobs_dirty, _publish_raw
from .meta import (
    _chosen_cover, _index_move, _index_set, _kind, _meta_changed, _meta_for, _meta_thumb,
    _note_folder, _prune_empty_dirs, _rel, _retag, _unique, _video_id, _write_json, _write_meta,
)
from .naming import _plan
from .images import _crop_square, _embed_cover, warm_thumbs
from .lyrics import _fetch_lyrics_bg
from .albums import _album_cover, _album_for
from .playlists import _pl_all, _pl_refs
from .index import _find_existing


def _update(job_id: str, **fields) -> None:
    with _lock:
        _jobs[job_id].update(fields)
    _jobs_dirty.set()
    if "status" in fields:
        _meta_changed()   # a finished job: look for what it filed


def _add_file(job_id: str, path: str) -> None:
    _meta_changed()
    _jobs_dirty.set()
    with _lock:
        files = _jobs[job_id]["files"]
        if path not in files:
            files.append(path)


def _hook_for(job_id: str):
    """yt-dlp progress hook: streams percent/speed/ETA into the job record."""

    def hook(d):
        with _lock:
            cancelled = _jobs[job_id].get("cancel")
        if cancelled:
            raise yt_dlp.utils.DownloadCancelled()

        status = d.get("status")
        info = d.get("info_dict") or {}
        fields: dict = {}

        if not _jobs[job_id].get("title"):
            fields["title"] = (
                info.get("playlist_title") or info.get("title") or _jobs[job_id]["url"]
            )

        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            fields.update(
                status="downloading",
                downloaded=done,
                total=total,
                speed=d.get("speed"),
                eta=d.get("eta"),
                current_file=Path(d.get("filename") or "").name or None,
                playlist_index=info.get("playlist_index"),
                playlist_count=info.get("playlist_count") or info.get("n_entries"),
            )
            if total:
                fields["percent"] = round(done / total * 100, 1)
            else:
                # fragmented downloads may not know the total size
                frag_count, frag_index = d.get("fragment_count"), d.get("fragment_index")
                fields["percent"] = (
                    round(frag_index / frag_count * 100, 1)
                    if frag_count and frag_index else None
                )

        with _lock:
            _jobs[job_id].update(fields)

    return hook


# Sponsor reads, "like & subscribe" and self-promotion are cut out of downloads
# using the community SponsorBlock database (it is sent a hashed id prefix only).
SPONSOR_CATEGORIES = ["sponsor", "selfpromo", "interaction"]


class _SafeSponsorBlockPP(SponsorBlockPP):
    """SponsorBlock that never fails a download (API down → keep the video uncut)."""

    def run(self, info):
        try:
            return super().run(info)
        except Exception as exc:  # noqa: BLE001
            self.report_warning(f"SponsorBlock unavailable, keeping the full video: {exc}")
            info["sponsorblock_chapters"] = []
            return [], info


class _SquareCoverPP(PostProcessor):
    """Music covers: YouTube shows square album art centred in a 16:9 frame;
    crop the thumbnail to its centre square before it's embedded."""

    def run(self, info):
        for t in info.get("thumbnails") or []:
            fp = t.get("filepath")
            if fp and os.path.exists(fp):
                _crop_square(Path(fp))
        return [], info


class _GrabDL(yt_dlp.YoutubeDL):
    """YoutubeDL with TubeGrab's library behaviour.

    * remembers the last extraction error (with `ignoreerrors` a failed
      extraction returns None and the reason only reaches report_error);
    * files every entry by _plan() — music, videos or playlists;
    * skips videos already in the library, moving a loose copy into the
      album/playlist being grabbed;
    * records metadata and the id → path index.
    """

    def __init__(self, params, *, job_id: str, playlist_mode: bool, kind: str, album: dict | None = None):
        super().__init__(params)
        self.album = album
        self.last_error: str | None = None
        self.job_id = job_id
        self.playlist_mode = playlist_mode
        self.media_kind = kind
        self.plans: dict[str, dict] = {}
        self._square = _SquareCoverPP(self)
        self.add_post_processor(_SafeSponsorBlockPP(self, categories=SPONSOR_CATEGORIES), when="after_filter")

    def report_error(self, message, *args, **kwargs):
        self.last_error = re.sub(r"^\s*ERROR:\s*", "", str(message)).strip()
        super().report_error(message, *args, **kwargs)

    def process_info(self, info_dict):
        audio = self.media_kind == "audio"
        plan = _plan(info_dict, audio=audio, playlist_mode=self.playlist_mode, album=self.album)
        vid = info_dict.get("id")
        ext = ".m4a" if audio else ".mp4"
        rel = _unique(plan["rel"], ext, vid)
        self.plans[vid] = plan
        _note_folder(str(Path(rel).parent.as_posix()), plan["collection_title"], plan["collection_url"])
        # music gets its tags from our plan (album, track number, album artist…)
        for k, v in plan["tags"].items():
            if v is not None:
                info_dict[k] = v   # "track" becomes the title tag
        if plan["tags"].get("release_year"):
            info_dict["meta_date"] = plan["tags"]["release_year"]   # date tag = release year
        self.params["outtmpl"]["default"] = str(DOWNLOAD_DIR / rel).replace("%", "%%") + ".%(ext)s"

        existing = _find_existing(vid, self.media_kind) if vid else None
        if existing:
            final = self._maybe_move(existing, rel, plan)
            _write_meta(info_dict, self._meta_extra(plan))
            if not _meta_thumb(vid):
                written = self._write_thumbnails("video", info_dict, self.prepare_filename(info_dict, "thumbnail"))
                if plan["section"] == "Music":
                    for fp, _ in written or []:
                        _crop_square(Path(fp))
            _index_set(vid, self.media_kind, _rel(final))
            _add_file(self.job_id, str(final))
            self.to_screen(f"[tubegrab] already in library: {_rel(final)}")
            return
        return super().process_info(info_dict)

    def pre_process(self, ie_info, key="pre_process", files_to_move=None):
        info, files_to_move = super().pre_process(ie_info, key, files_to_move)
        if key == "before_dl" and self.plans.get(info.get("id"), {}).get("section") == "Music":
            info = self.run_pp(self._square, info)
        return info, files_to_move

    @staticmethod
    def _meta_extra(plan: dict) -> dict:
        t = plan["tags"]
        return {"section": plan["section"], "album_artist": t.get("album_artist"),
                "album": t.get("album"), "release_year": t.get("release_year"),
                "track_number": t.get("track_number"),
                "track": t.get("track") if plan["section"] == "Music" else None,
                "extras": 1}   # chapters + captions fetched

    def _maybe_move(self, existing: Path, rel: str, plan: dict) -> Path:
        """Pull a loose copy into the album/playlist now being grabbed."""
        cur = _rel(existing)
        dest = DOWNLOAD_DIR / (rel + existing.suffix)
        if existing == dest or dest.exists() or _meta_for(_video_id(existing)).get("placed") == "user":
            return existing   # (you moved it there yourself: it stays)
        cur_section = cur.split("/")[0]
        legacy = cur_section not in SECTIONS
        if plan["section"] == "Music":
            # a loose single slots into its numbered album position
            ok = bool(plan["tags"].get("track_number")) and (
                legacy or (cur_section == "Music" and not INDEX_PREFIX_RE.match(existing.name)))
        elif plan["section"] == "Playlists":
            ok = legacy or cur_section == "Videos"   # never pull tracks out of albums
        else:
            ok = legacy
        if not ok:
            return existing
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(existing), str(dest))
        _prune_empty_dirs(existing.parent)
        _index_move(cur, _rel(dest))
        if plan["section"] == "Music":
            t = plan["tags"]
            _retag(dest, {"title": t.get("track"), "album": t.get("album"), "albumartist": t.get("album_artist"),
                          "tracknumber": t.get("track_number"), "date": t.get("release_year")})
        return dest


def _pp_hook_for(job_id: str, ydl_ref: dict):
    """Postprocessor hook: record the final (merged/extracted) file + metadata."""

    def hook(d):
        if d.get("status") != "finished":
            return
        info = d.get("info_dict") or {}
        final = info.get("filepath")
        if final and Path(final).suffix.lower() in MEDIA_EXTS and Path(final).exists():
            _add_file(job_id, final)
            plan = (ydl_ref.get("ydl").plans if ydl_ref.get("ydl") else {}).get(info.get("id"), {})
            _write_meta(info, _GrabDL._meta_extra(plan) if plan else None)
            if info.get("id"):
                _index_set(info["id"], _kind(Path(final)), _rel(Path(final)))
            if d.get("postprocessor") == "EmbedThumbnail":
                chosen = _chosen_cover(Path(final))
                if chosen and chosen[1] != info.get("id"):
                    _embed_cover(Path(final), chosen[0])
                if plan.get("section") == "Music":
                    _fetch_lyrics_bg(Path(final))

    return hook


def _base_opts(fmt: str) -> dict:
    opts: dict = {
        "format": FORMATS[fmt],
        # within the resolution cap prefer efficient codecs, then plain HTTPS
        # over HLS (HLS variants are often 1.5–2× bigger for the same picture)
        "format_sort": ["res", "fps", "vcodec", "proto"],
        "outtmpl": {
            "default": str(DOWNLOAD_DIR / "%(title)s.%(ext)s"),   # replaced per entry
            "thumbnail": str(META_DIR / "%(id)s.%(ext)s"),
            "pl_thumbnail": str(META_DIR / "%(id)s.%(ext)s"),
            "subtitle": str(META_DIR / "%(id)s.%(ext)s"),
        },
        "writethumbnail": True,
        "js_runtimes": JS_RUNTIMES,
        "ignoreerrors": True,      # one bad entry must not kill a playlist job
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 5,
        "concurrent_fragment_downloads": 4,
        "windowsfilenames": True,
    }
    cut = {"key": "ModifyChapters", "remove_sponsor_segments": SPONSOR_CATEGORIES}   # must run first
    if fmt == "audio":
        opts["postprocessors"] = [
            cut,
            # jpg is what every player understands for embedded cover art
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
            # remux to .m4a; only transcodes (to AAC) if the source wasn't AAC already
            {"key": "FFmpegExtractAudio", "preferredcodec": "m4a"},
            {"key": "FFmpegMetadata", "add_metadata": True},   # title/artist/album/track tags
            # must run after tagging (ffmpeg's rewrite would drop it); keep the
            # copy in .meta/ for the library UI
            {"key": "EmbedThumbnail", "already_have_thumbnail": True},
        ]
    else:
        opts["merge_output_format"] = "mp4"
        opts.update(SUB_OPTS)   # captions → transcript, CC and search
        opts["postprocessors"] = [
            cut,
            {"key": "FFmpegMetadata", "add_metadata": False, "add_chapters": True},   # chapters for other players too
        ]
    if COOKIES_FILE.exists():
        opts["cookiefile"] = str(COOKIES_FILE)
    return opts


# Downloads run two at a time (TUBEGRAB_PARALLEL); the rest wait as "queued".
# More in parallel isn't faster on a small NAS: they share one CPU and one link.
_job_slots = threading.Semaphore(max(1, int(os.environ.get("TUBEGRAB_PARALLEL") or 2)))


def _run_job(job_id: str, url: str, fmt: str, playlist: bool) -> None:
    while not _job_slots.acquire(timeout=1):
        if _jobs[job_id].get("cancel"):
            _update(job_id, status="cancelled", finished_at=_now())
            return
    try:
        _run_job_slot(job_id, url, fmt, playlist)
    finally:
        _job_slots.release()
        _tidy_when_idle()


def _run_job_slot(job_id: str, url: str, fmt: str, playlist: bool) -> None:
    if _jobs[job_id].get("cancel"):
        _update(job_id, status="cancelled", finished_at=_now())
        return

    ref: dict = {}
    opts = _base_opts(fmt)
    opts.update(
        progress_hooks=[_hook_for(job_id)],
        postprocessor_hooks=[_pp_hook_for(job_id, ref)],
        noplaylist=not playlist,
    )

    try:
        album = _album_for(url, opts) if fmt == "audio" and playlist else None
        with _GrabDL(opts, job_id=job_id, playlist_mode=playlist,
                     kind="audio" if fmt == "audio" else "video", album=album) as ydl:
            ref["ydl"] = ydl
            info = ydl.extract_info(url, download=True)
        entries = (
            [e for e in (info.get("entries") or []) if e]
            if info and info.get("_type") == "playlist" else [info] if info else []
        )
        if not entries:
            raise RuntimeError(
                ydl.last_error or "Nothing was downloaded — check the URL."
            )
        if any(pl.get("mixed_playlist") for pl in ydl.plans.values()):
            _mirror_playlist(info, _jobs[job_id].get("files", []))
        if album:
            for key in {Path(p["rel"]).parent.as_posix() for p in ydl.plans.values() if p["section"] == "Music"}:
                _album_cover(key, album)
        _update(
            job_id,
            status="complete",
            title=info.get("playlist_title") or info.get("title") or url,
            percent=100.0, speed=None, eta=None, current_file=None,
            entry_count=len(entries), finished_at=_now(),
            error=ydl.last_error,   # a track that failed (bin/grab retries one YouTube rate-limited)
        )
    except yt_dlp.utils.DownloadCancelled:
        _update(job_id, status="cancelled", finished_at=_now())
    except Exception as exc:  # noqa: BLE001 — surface the message in the UI
        _update(job_id, status="error", error=str(exc), finished_at=_now())


def _mirror_playlist(info: dict, paths: list[str]) -> None:
    """Songs from a mixed YouTube playlist were filed into their albums; keep the
    playlist itself as one of your playlists (same name and order)."""
    names = [_rel(Path(p)) for p in paths
             if Path(p).is_file() and Path(p).suffix.lower() in MEDIA_EXTS]
    refs = _pl_refs(list(dict.fromkeys(names))) or []
    source = info.get("id") or info.get("webpage_url")
    if not refs or not source:
        return
    with _meta_lock:
        pls = _pl_all()
        pid = next((k for k, p in pls.items() if p.get("source") == source), None)
        if pid:   # downloaded again: add what's new, keep your order
            p = pls[pid]
            p["items"] = p.get("items", []) + [r for r in refs if r not in p.get("items", [])]
        else:
            pid = uuid.uuid4().hex[:10]
            p = pls[pid] = {"title": (info.get("title") or "Playlist")[:120], "description": "",
                            "items": refs, "cover": None, "created": _now(), "source": source}
        p["updated"] = _now()
        _write_json(PLAYLISTS_FILE, pls)


def _tidy_when_idle() -> None:
    """Tidy the music library once no download is writing into it."""
    with _lock:
        busy = any(j.get("status") in ("queued", "running", "downloading", "processing") for j in _jobs.values())
    if not busy:
        from .maintenance import tidy_music   # (deferred: maintenance builds on this module)
        try:
            tidy_music()
            warm_thumbs()
        except Exception as exc:  # noqa: BLE001 — never fail a job over housekeeping
            print(f"[tubegrab] tidy failed: {exc}")


KEEP_FINISHED = 50


def _forget_old_jobs() -> None:
    """Keep only the newest finished jobs: the whole list goes to every open page
    once a second while something downloads, and each of its files is checked."""
    with _lock:
        done = sorted((j for j in _jobs.values() if j.get("finished_at")), key=lambda j: j["finished_at"])
        for j in done[:-KEEP_FINISHED]:
            del _jobs[j["id"]]


def serialize(job: dict) -> dict:
    public = {k: v for k, v in job.items() if k != "cancel"}
    out = []
    for f in public.get("files", []):
        if not Path(f).exists():   # intermediate streams yt-dlp already removed, or moved
            continue
        try:
            out.append(_rel(Path(f)))
        except ValueError:
            out.append(Path(f).name)
    public["files"] = out
    return public


def _jobs_payload() -> str:
    with _lock:
        jobs = [serialize(j) for j in _jobs.values()]
    jobs.sort(key=lambda j: j["created_at"], reverse=True)
    return json.dumps(jobs, separators=(",", ":"))


def _jobs_publisher() -> None:
    """Download progress changes many times a second; send it at most once a second."""
    while True:
        _jobs_dirty.wait()
        _jobs_dirty.clear()
        _publish_raw("jobs", _jobs_payload())
        time.sleep(1)
