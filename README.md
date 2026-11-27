# TubeGrab

A tiny local web UI around [yt-dlp](https://github.com/yt-dlp/yt-dlp): paste a
YouTube video or playlist link (your own uploads or content you are licensed to
download), pick a quality, and the file is downloaded into `downloads/` ready
for further processing (e.g. feeding into Media-Transcription).

## Run

```bash
./run.sh          # sets up .venv + bun deps, builds the UI if needed, starts server
./run.sh --dev    # Flask API on :8765 + Vite hot-reload UI on http://127.0.0.1:5173
```

Requires Python 3, [bun](https://bun.sh), and ffmpeg.

Open **http://127.0.0.1:8765** — the server binds to `0.0.0.0` by default, so
it is also reachable from other devices on your LAN at `http://<this-host-ip>:8765`.
Set `TUBEGRAB_HOST` / `TUBEGRAB_PORT` to change the binding.

> No authentication: anyone on your network can queue downloads and delete
> library files. Keep it off untrusted networks.

- Requires **ffmpeg** on PATH (already installed on this machine) — yt-dlp uses
  it to merge video+audio streams and to package audio as M4A.
- **Automatic library layout** — every download is filed from YouTube's own
  metadata; nothing to sort by hand:

  ```
  Music/<Artist>/<Album (Year)>/01 - Track.m4a    audio: album tracks & singles
  Music/<Artist>/Singles/Track.m4a                audio without album info
  Videos/<Channel>/Title.mp4                      single videos
  Playlists/<Playlist>/001 - Title.mp4            any other video playlist
  .meta/                                          id index, metadata, thumbnails
  ```

  Audio gets square cover art and full tags (artist, album artist, album,
  track number, year). Nothing is downloaded twice: a song grabbed on its own
  moves into its numbered album slot when you later grab the album. If YouTube
  got a name wrong, **Edit** on the album/channel/playlist page renames the
  folder and rewrites the tags.

  Audio is always music. A playlist of fan uploads ("Artist – Album (full
  album)") is recognised as one album and checked against MusicBrainz (free,
  open; only the guessed artist and album name are sent) for the real track
  names, numbers, year and cover. The odd lecture grabbed as audio: **Move to
  Videos** in its `⋯` menu (and **Move to Music** the other way), for one item
  or a whole album/playlist; tidying never moves it back.

## Run on a Synology NAS (Docker)

Needs a model with **Container Manager** (most "+" models).

1. Create a shared folder for the library, e.g. `media` → `/volume1/media/youtube`.
2. Copy this project folder to the NAS, e.g. `/volume1/docker/tubegrab`
   (File Station or SMB; `.venv`, `downloads`, `web/node_modules` aren't needed).
3. Edit `docker-compose.yml`: the volume path, and `PUID`/`PGID` = the DSM user
   that should own the files (SSH: `id <user>`; usually `1026` / `100`).
4. Container Manager → **Project** → **Create** → path `/volume1/docker/tubegrab`,
   "Use existing docker-compose.yml" → build & start (first build takes a few minutes).
5. Open `http://<nas-ip>:8765`.

The container updates yt-dlp on every start, so **restarting it is the fix for
most download failures**. To update TubeGrab itself, copy the new files over and
rebuild the project. Settings (env vars): `TUBEGRAB_DOWNLOADS` (library folder),
`TUBEGRAB_PORT`, `PUID`/`PGID`, `YTDLP_AUTOUPDATE`, optional cookies at
`/config/cookies.txt`.

Without Container Manager on the NAS, build here and import the image instead:
`docker save tubegrab:latest | gzip > tubegrab.tar.gz` → Container Manager →
Image → Import (the NAS must have the same CPU architecture).

## Features

- Two choices, both size-conscious: **Video** is always 720p with a bitrate
  cap and the most efficient codec (AV1 → VP9 → H.264), ≈ 75 MB/h for
  slides/animation, up to ~500 MB/h; **Audio** is M4A (YouTube's original AAC,
  no re-encode, ≈ 1 MB/min, with cover art + tags)
- Full playlist support: one job downloads every entry, with per-entry
  progress (`entry 3/25`); one broken entry doesn't kill the job. Two
  downloads run at a time, the rest queue (`TUBEGRAB_PARALLEL`)
- Live progress: percent, speed, ETA, current file; a **Stop** button cancels
  a running download
- **Search or grab** from anywhere: `⌘K` / `/` opens one box that finds
  artists, albums, songs, channels, playlists and videos (accents, case and
  punctuation don't matter: "bjork", "acdc"); a pasted link turns it into the
  download form. Pasting a link on any page opens it with the link in.
- **Music** (`/music`): Albums, Artists and Songs tabs (the last one used is
  remembered), sorted by name with A–Z headings and a jump rail (drag it on a
  phone), or by year with decades; back returns to the same spot. **Favorites**:
  heart artists, albums and songs (artist and album pages, track rows, the
  player); the Favorites switch shows just those in each tab. Album pages
  with track lists; **Videos** (`/videos`): channels, playlists, recent videos.
  Play all / Shuffle everywhere; Up next follows album/playlist order
- **Library** (`/`) like a personal YouTube: thumbnail grid
  (poster frames generated with ffmpeg and cached in `.meta/thumbs/`), search,
  sort/filter, durations (ffprobe), click-to-play streaming with seeking
  (HTTP Range), "Up next" list, autoplay-next, save/delete actions
- **Your own playlists** (`/playlists`): mix music and videos, drag to
  reorder (touch too), name + description, cover from any item or an uploaded
  picture (otherwise a mosaic). Add from any track/video/album's `⋯` menu.
  Playlists only reference files (stored in `.meta/playlists.json`), so a track
  can be in many; playing from a playlist follows its order
- **Learning features**: resume where you stopped (synced across devices),
  progress bars + watched marks ("4/12 watched" on courses), playback speed
  (videos only; music stays 1×), YouTube chapters (seek-bar marks + list),
  English captions (CC, a click-to-seek transcript, and search inside what's
  said in your videos from the Videos search). Sponsor reads, self-promotion and
  "like & subscribe" bits are cut out at download via SponsorBlock
- **Lyrics** for songs from LRCLIB (free, open; only artist/title/album/length
  are sent): synced lyrics follow along and are tappable, plain ones show as
  text; also embedded in the .m4a. Looked up on download (existing songs once,
  in the background); ⋯ → Find lyrics retries
- **Player controls** like YouTube's. Phone: double-tap the left/right side to
  skip 10 s (keep tapping to stack), tap for prev / play / next. Desktop
  keys: `Space`/`k` play, `j`/`l` ±10 s, `←`/`→` ±5 s, `↑`/`↓` volume, `m` mute,
  `f` full screen, `0`–`9` jump, `Shift+N`/`Shift+P` next/previous. Previous
  restarts the track after 3 s, else goes back (album order, then history).
  Lock-screen / headphone controls do next, previous and seek too
- **Phones**: bottom tab bar, docked mini player, add to home screen for a
  full-screen app
- Downloads page (`/downloads`): live job cards plus a file manager
- Built to stay fast at 50k+ items on a small NAS:
  - **SQLite index** (`.meta/library.db`): one row per file and per collection,
    kept up to date incrementally — folder mtimes say which folders changed,
    metadata writes mark exactly the rows they affect, and a once-a-minute check
    catches changes made over SMB / File Station. It's only an index: delete it
    and it's rebuilt. Edits return once the index has them.
  - **The device asks for what it shows**: collections + totals come whole
    (`/api/library`), everything else is queried and paged (`/api/files`,
    `/api/watch/<file>` for what plays next, `/api/search` for captions).
  - **Opens instantly**: answers are cached on the device (IndexedDB) and
    re-checked with ETags; a service worker keeps the app shell and thumbnails
    (on https, e.g. through a reverse proxy).
  - **Live updates** over server-sent events (`/api/events`): library changes,
    progress from other devices, download status — no polling.
  - **Thumbnails**: grid-sized copies with a placeholder colour, at permanent
    URLs (`?v=<version>`, cached for a year).
  - **Caption search** uses SQLite full-text search (word forms: "derive"
    finds "derivative"; partial last word).
  - **Video seeking** is sent straight from disk (sendfile), never buffered.
  - Measured with 50,000 items (40k songs, 10k videos): first index ≈ 45 s on a
    desktop CPU, list queries ≈ 25 ms, unchanged checks < 1 ms, every screen
    under 0.7 s with a phone-speed CPU.

## Updating yt-dlp

YouTube changes frequently; if downloads start failing with extraction
errors, update yt-dlp (this is the most common fix):

```bash
.venv/bin/pip install -U yt-dlp
```

## Cookies (only if needed)

If YouTube answers with "Sign in to confirm you're not a bot" for some
content (usually only matters for age-restricted or member videos), export
your browser cookies in Netscape format to `cookies.txt` in this folder and
restart — it is picked up automatically.

## Command line

`bin/grab` queues downloads from a terminal (Python 3 standard library only, so
it can be copied anywhere). Give it files with one link per line, links, or
both; blank lines and `#` lines are skipped. The server downloads two at a time
and queues the rest.

```sh
bin/grab links.txt              # audio (the default)
bin/grab -v links.txt more.txt  # video
bin/grab -p <playlist link>     # whole playlists, as an album or playlist
pbpaste | bin/grab -v -w        # links from stdin; wait and report each one
```

For long lists, `--slow` keeps YouTube from rate-limiting the batch partway
through. YouTube allows about 300 videos an hour per connection. grab then
sends one link at a time and pauses between them (60 s, `--pause` to change);
it has to keep running:

```sh
nohup bin/grab -p --slow albums.txt > grab.log 2>&1 &
tail -f grab.log
```

If YouTube limits it anyway, it waits an hour and tries that link again, even
an album that was cut short. If the server is down (a deploy, a reboot), it
waits until the server is back. Finished links go into `albums.txt.done`, so
running the same command again carries on where it left off.

It talks to `$TUBEGRAB_URL`, else `http://192.168.254.10:8765`; `-s` overrides
both. `ln -s "$PWD/bin/grab" ~/.local/bin/grab` puts it on your PATH.

### Playlists by an agent

`.claude/skills/playlist/` is a Claude Code skill: ask an agent for "an hour of
90s alternative from my library" or "my most played, no more than two songs per
artist" and it reads the library, picks songs and creates the playlist (you get
the link). Its `tg.py` works on its own too: `tg.py stats`, `tg.py songs
--genre jazz --sort plays`, `… | tg.py create "Title" -`. To use it from any
folder: `ln -s "$PWD/.claude/skills/playlist" ~/.claude/skills/playlist`.

What it knows per song: artist, album, year, length, favorites, how often and
when you played it (a play: half the song or 4 minutes), and genres, which the
NAS fetches from MusicBrainz in the background (the album's, else the artist's;
only names are sent).

## Legal note

Only download videos you uploaded yourself or that you otherwise have the
rights to download. This tool is for personal archival of your own content.

## Files

| Path | Purpose |
| --- | --- |
| `app.py` | Entry point (`gunicorn app:app`, `./run.sh`): starts the server and its background threads |
| `tubegrab/` | The backend, one module per area; the map of modules and their layers is in `tubegrab/__init__.py` |
| `web/` | React + TypeScript UI (Vite, Tailwind, bun). `src/pages/` has Library, Watch, Downloads |
| `static/placeholder.svg` | Fallback thumbnail |
| `downloads/` | Library (`TUBEGRAB_DOWNLOADS`); `.meta/` holds metadata, thumbnails, caches |
| `run.sh` | venv bootstrap + launch (local) |
| `bin/grab` | Command-line client: queue links from files or stdin |
| `tests/` | pytest suite: filing, album recognition, moves, path safety, parsers (no network) |
| `Dockerfile`, `docker/entrypoint.sh`, `docker-compose.yml` | container build + NAS deployment |

## Tests

```sh
.venv/bin/pip install -r requirements-dev.txt   # once
.venv/bin/python -m pytest
```

Each test gets an empty throwaway library; MusicBrainz, LRCLIB and the
background threads are off. A push deploys, so `git push` runs them (plus
pyflakes and the UI type check) first and stops if anything fails; `git push --no-verify` skips
that. The hook lives in `.githooks/`; a fresh clone enables it with
`git config core.hooksPath .githooks`.

## Deploying updates (git push → NAS)

The NAS holds a bare repo at `/volume1/git/tubegrab.git`. Its `post-receive`
hook checks `main` out into `/volume1/docker/tubegrab` (the Container Manager
project), rebuilds the image and restarts the container; build output shows
up in your terminal. A failed build leaves the running version alone.

```sh
git commit -am "…"
git push            # GitHub (origin)
git push nas main   # deploys: ssh://root@192.168.254.10/volume1/git/tubegrab.git
```

Roll back with `git revert <commit> && git push`.

