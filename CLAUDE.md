# TubeGrab

Self-hosted YouTube (and SoundCloud) library around yt-dlp. Flask + gunicorn
backend, React 19 / Vite / Tailwind v4 / TypeScript UI built with bun. Runs in
Docker on a Synology DS223 (aarch64, 2 GB RAM, not upgradable). Public at
tube.putty-ai.com behind Pangolin, which handles login: don't add app auth.

## Layout

- `app.py`: entry point only (`gunicorn app:app`, `./run.sh`); starts the background threads.
- `tubegrab/`: the backend. The module map and its layers are in
  `tubegrab/__init__.py`. A module imports only from modules above it on that
  map (`tests/test_layers.py` enforces this). The two upward calls are deferred
  imports inside functions; add one only for a real callback, never to dodge
  the layering.
- `tubegrab/api/`: Flask routes, one blueprint per area. Keep logic in the modules, not the routes.
- `web/src/`: `pages/`, `components/`, `api.ts` (every endpoint the UI calls), `store.tsx`.
- `web/public/sw.js`: service worker. Page loads are network-first, so a new build shows on the next load.
- Library on disk: `Music/<Artist>/<Album (Year)>/NN - Track.m4a`,
  `Music/<Artist>/Singles/`, `Videos/<Channel>/`, `Playlists/<Playlist>/NNN - …`,
  plus `.meta/` (index.json, folders.json, `<id>.json` metadata, thumbnails, library.db).

## Commands

```sh
./run.sh                          # local: build UI if needed, serve on :8765
./run.sh --dev                    # Flask :8765 + Vite hot reload :5173
.venv/bin/python -m pytest        # tests (about 2 s, no network)
.venv/bin/python -m pyflakes app.py bin/grab tubegrab tests
cd web && bun run typecheck
```

`TUBEGRAB_DOWNLOADS=<dir>` points the app at another library; use a copy, never the NAS share.

## Tests

- Every bug fix gets a test that fails without the fix. Check it does by undoing the fix.
- `tests/conftest.py` gives each test an empty library and blocks the network:
  MusicBrainz, LRCLIB and cover art are faked.
- To replace a function the other modules import, use `patch(monkeypatch, name, value)`,
  which replaces it everywhere. Plain `monkeypatch.setattr` on one module misses the copies.
- `reindex()` runs the indexer once. Routes that read the SQLite index (playlists,
  `/api/files`, search, progress) need it after `add_file`.

## Deploying

- `git push` goes to GitHub (`origin`). `git push nas main` deploys: the NAS
  hook rebuilds the image and restarts the container in about 10–40 s. A failed
  build leaves the running version alone.
- The pre-push hook (`.githooks/pre-push`) runs pyflakes, the tests and the UI
  type check, and blocks the push on failure. A fresh clone enables it with
  `git config core.hooksPath .githooks`.
- Before deploying, check no download is running:
  `curl -s http://192.168.254.10:8765/api/jobs` must show nothing `queued` or `downloading`.
- Commit and deploy only when asked. Commit messages say why, not just what.
- On the NAS: `/usr/local/bin/docker`, the container is named `tubegrab`, the
  library is at `/volume1/chunt/media/YouTube`. Check after a deploy with
  `docker logs --since 2m tubegrab`.

## Behaviour to keep

- Audio is always music. Without music metadata, a song goes to
  `Music/<Artist>/Singles` (artist from an "Artist - Song" title, else the channel).
- Fan-upload playlists are recognised as albums via MusicBrainz; answers are cached in `.meta/albums.json`.
- `"placed": "user"` in a file's metadata means the user moved it. Tidy,
  refile and re-download must never move it back.
- Moves, renames and refiling update `index.json` (`_index_move`) and folder
  records (`_carry_folder`, `_forget_gone_folders`). A file must never be overwritten: use `_unique`.
- Anything reachable by URL takes untrusted input: resolve paths with
  `files._safe_path`, and pass ffmpeg only files whose format was checked
  (see `images._image_demuxer`).
- The NAS is small: no per-request walks of the library (ask the SQLite index),
  keep ffmpeg runs bounded, and keep downloads at 2 in parallel.
- Video: 720p, AV1/VP9 preferred. AV1 plays fine on the user's devices.

## UI rules

- No text explaining features: no empty-state instructions, dialog subtitles,
  hints or tips. Headings, data and controls only.
- Minimal look, few rounded corners. Destructive and rare actions go in ⋯ menus.
- No "Open on YouTube" buttons.
