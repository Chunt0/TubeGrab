#!/usr/bin/env python3
"""
TubeGrab — a tiny local web UI around yt-dlp.

Paste a YouTube video or playlist URL (your own uploads or content you are
licensed to download), pick a quality, and the file is filed into the library
automatically:

    Music/<Artist>/<Album (Year)>/<NN - Track>.m4a     audio (album or single)
    Music/<Artist>/Singles/<Track>.m4a                 audio without album info
    Videos/<Channel>/<Title>.mp4                       single videos
    Playlists/<Playlist>/<NNN - Title>.mp4             any other video playlist
    .meta/                                             index, metadata, thumbnails

The code lives in the tubegrab/ package (see its __init__ for a map).

Run:  ./run.sh   →  http://127.0.0.1:8765      (gunicorn: app:app)
"""

import os

from tubegrab.server import app, start_background

start_background()

if __name__ == "__main__":
    app.run(
        host=os.environ.get("TUBEGRAB_HOST", "0.0.0.0"),
        port=int(os.environ.get("TUBEGRAB_PORT", "8765")),
        debug=False,
        threaded=True,
    )
