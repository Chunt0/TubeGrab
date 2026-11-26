#!/bin/sh
# TubeGrab container entrypoint: refresh yt-dlp, fix ownership, drop privileges.
set -e

# YouTube changes often and an outdated yt-dlp is the #1 cause of failures,
# so pull the latest release on every start (skip with YTDLP_AUTOUPDATE=0).
if [ "${YTDLP_AUTOUPDATE:-1}" = "1" ]; then
    echo "[tubegrab] updating yt-dlp…"
    pip install --no-cache-dir --quiet --upgrade "yt-dlp[default]" \
        || echo "[tubegrab] yt-dlp update failed (offline?), using bundled version"
fi
echo "[tubegrab] yt-dlp $(python -c 'import yt_dlp;print(yt_dlp.version.__version__)')"

# Files are created as PUID:PGID (default 1026:100 = first Synology user : users),
# group-writable so they're editable over SMB.
mkdir -p "$TUBEGRAB_DOWNLOADS/.meta/thumbs"
chown "$PUID:$PGID" "$TUBEGRAB_DOWNLOADS" 2>/dev/null || true
chown -R "$PUID:$PGID" "$TUBEGRAB_DOWNLOADS/.meta" 2>/dev/null || true
umask 002

echo "[tubegrab] serving on :$TUBEGRAB_PORT as uid=$PUID gid=$PGID, library at $TUBEGRAB_DOWNLOADS"
# one worker: jobs live in memory; threads serve streams + API concurrently
exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups \
    gunicorn app:app \
        --bind "0.0.0.0:$TUBEGRAB_PORT" \
        --workers 1 --threads 64 --timeout 0 \
        --access-logfile - --error-logfile -
