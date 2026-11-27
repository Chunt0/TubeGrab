"""TubeGrab: a self-hosted YouTube library around yt-dlp.

Modules, lowest layer first (a module imports only from those above it in this
list; the two exceptions are deferred imports, marked where they are made):

    config        paths, formats, shared locks
    events        change notifications for /api/events
    meta          the .meta store: index.json, folders.json, per-video metadata
    naming        where a download goes (_plan), safe names, track titles
    files         safe library paths, ffprobe, collection kinds
    db            the SQLite library index: schema, connection, row output
    httputil      cached JSON answers
    images        thumbnails, covers, placeholder colours
    transcripts   captions → timed text
    lyrics        LRCLIB lookups
    albums        recognising fan-upload playlists as albums (MusicBrainz)
    playlists     your playlists
    favorites     hearted artists, albums and songs
    index         the background indexer
    jobs          downloads (yt-dlp)
    moves         Music ⇄ Videos
    maintenance   startup and after-download housekeeping
    api/          Flask routes, one blueprint per area
    server        the Flask app, the built UI, background threads

app.py (next to this package) is the entry point: gunicorn app:app, or ./run.sh.
"""
