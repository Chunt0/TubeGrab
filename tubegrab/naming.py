"""Where a download goes (_plan) and the name clean-up it relies on: safe file
names, track titles without '(Official Video)' noise, artist and album folders
that reuse an existing spelling."""

import re

from .config import DOWNLOAD_DIR, SINGLES, YEAR_RE
from .meta import _folders


def _clean(name: str | None, fallback: str = "Unknown", limit: int = 120) -> str:
    """A file/folder name that's safe on Linux, Windows and SMB shares."""
    s = (name or "").replace(":", " -").replace("/", "-").replace("\\", "-")
    s = re.sub(r"\s", " ", s)               # (tabs, line breaks: a space, not nothing)
    s = re.sub(r'[<>"|?*\x00-\x1f]', "", s)
    s = re.sub(r"\s+", " ", s).strip().rstrip(". ")
    s = s.lstrip(".")                       # no hidden files
    return (s[:limit].rstrip(". ") or fallback)


def _clean_channel(name: str | None) -> str | None:
    """'Coldsteel - Topic' → 'Coldsteel' (YouTube's auto-generated artist channels)."""
    return re.sub(r" - Topic$", "", name) if name else name


def _year(info: dict) -> str | None:
    y = info.get("release_year") or (info.get("release_date") or "")[:4]
    return str(y) if y and str(y).isdigit() else None


def _artist(info: dict) -> str | None:
    """Album artist for folder names: the first credited artist."""
    for key in ("album_artist", "album_artists", "artists", "artist", "creators"):
        v = info.get(key)
        if isinstance(v, list):
            v = v[0] if v else None
        if v:
            return str(v).split(", ")[0] if key == "artist" else str(v)
    return None


# "(Official Music Video)", "[Official Lyric Video]", "(Visualizer)", "(Official Audio HD)"…
_NOISE_WORDS = re.compile(r"^(?:official|music|lyrics?|audio|video|visuali[sz]er|mv|hd|4k|\s|-)+$", re.I)


def _track_title(title: str, artist: str | None) -> str:
    """'Taylor Swift - Fortnight (feat. Post Malone) (Official Music Video)' → 'Fortnight (feat. Post Malone)'.
    Keeps meaningful brackets like '(feat. …)', '(Live at …)', '(Remix)'."""
    t = title
    if artist:
        t = re.sub(rf"^\s*{re.escape(artist)}\s*[-–—:|]\s*", "", t, flags=re.I)
    t = re.sub(r"\s*[\(\[]([^\)\]]*)[\)\]]",
               lambda m: "" if _NOISE_WORDS.match(m.group(1)) else m.group(0), t)
    t = re.sub(r"\s+", " ", t).strip(" -–—")
    return t or title


def _fold(name: str) -> str:
    """Comparison key for artist/album names: 'MELVINS' == 'Melvins'."""
    return _clean(name).casefold()


def _artist_dir(artist: str) -> str:
    """Existing Music/<artist> folder spelled differently ('MELVINS' vs 'Melvins'), else a new name."""
    music = DOWNLOAD_DIR / "Music"
    if music.is_dir():
        for d in music.iterdir():
            if d.is_dir() and _fold(d.name) == _fold(artist):
                return d.name
    return _clean(artist)


def _album_folder(artist: str, album: str, year: str | None, url: str | None) -> tuple[str, str, str | None]:
    """Where an album lives, reusing an existing folder so tracks whose metadata
    disagrees (YouTube gives some tracks a year or a differently-cased artist,
    others not) don't split the album. Returns (artist folder, album folder, year)."""
    adir = _artist_dir(artist)
    base = _fold(album)
    candidates: list[tuple[str, str]] = []
    for key, v in _folders().items():
        parts = key.split("/")
        if len(parts) == 3 and parts[0] == "Music" and (DOWNLOAD_DIR / key).is_dir() and (
                (url and v.get("url") == url) or
                (_fold(parts[1]) == _fold(adir) and _fold(YEAR_RE.sub("", parts[2])) == base)):
            candidates.append((parts[1], parts[2]))
    d = DOWNLOAD_DIR / "Music" / adir
    if d.is_dir():
        candidates += [(adir, p.name) for p in d.iterdir()
                       if p.is_dir() and _fold(YEAR_RE.sub("", p.name)) == base]
    if not candidates:
        return adir, (f"{_clean(album)} ({year})" if year else _clean(album)), year
    pick = next((c for c in candidates if YEAR_RE.search(c[1])), candidates[0])   # prefer one with a year
    m = YEAR_RE.search(pick[1])
    return pick[0], pick[1], (m.group(1) if m else year)


def _is_album_playlist(info: dict) -> bool:
    # YouTube Music's auto-generated album playlists
    return str(info.get("playlist_id") or "").startswith("OLAK5uy_")


def _plan(info: dict, *, audio: bool, playlist_mode: bool, album: dict | None = None) -> dict:
    """Decide where an entry goes. Returns {rel (no extension), section, tags}.
    `album`: this playlist was identified as one album (_identify_album)."""
    title = info.get("track") or info.get("title") or info.get("id") or "Untitled"
    in_playlist = playlist_mode and bool(info.get("playlist_id"))
    album_pl = in_playlist and _is_album_playlist(info)
    artist = _artist(info)
    tags: dict = {}

    has_album = bool(info.get("album") and artist)
    # a regular playlist that is really one album ("The Smashing Pumpkins: Gish (Deluxe Edition)")
    album_like = album_pl or (in_playlist and has_album and len(_fold(info["album"])) >= 3
                              and _fold(info["album"]) in _fold(info.get("playlist_title") or ""))
    if audio and in_playlist and album and not has_album and not album_pl:
        # fan uploads of one album: Music/Artist/Album (Year)/NN - Track, as the album has it
        t = album["tracks"].get(info.get("id")) or {
            "no": None, "title": _local_title(title, album.get("guess_artist"), album.get("guess_album"))}
        adir, folder, year = _album_folder(album["artist"], album["album"], album.get("year"), album.get("url"))
        no = t.get("no")
        name = f"{int(no):02d} - {_clean(t['title'])}" if no else _clean(t["title"])
        tags = {"artist": adir, "album_artist": adir, "album": album["album"], "track_number": no,
                "release_year": year, "track": t["title"]}
        return {"rel": f"Music/{adir}/{folder}/{name}", "section": "Music", "tags": tags, "mixed_playlist": False,
                "collection_title": album["album"], "collection_url": album.get("url")}
    # audio goes to Music when YouTube knows the song; a mixed playlist's songs
    # land in their own albums (and become one of your playlists, see _run_job)
    if audio and (album_like or artist) and (not in_playlist or album_like or has_album):
        # Music: Artist / Album (Year) / NN - Track
        artist = artist or _clean_channel(info.get("uploader") or info.get("channel"))
        album = info.get("album") or (info.get("playlist_title") if album_pl else None)
        year = _year(info)
        if not info.get("track"):   # a music video, not a YouTube Music track
            title = _track_title(title, artist)
        track_no = info.get("playlist_index") if album_like else info.get("track_number")
        if album:
            url = info.get("playlist_webpage_url") if album_like else None
            adir, folder, year = _album_folder(artist, album, year, url)
        else:
            adir, folder = _artist_dir(artist), SINGLES
        name = f"{int(track_no):02d} - {_clean(title)}" if track_no and album else _clean(title)
        rel = f"Music/{adir}/{folder}/{name}"
        artist = adir   # album artist = the folder's spelling, so tags agree across the album
        tags = {"artist": info.get("artist") or artist, "album_artist": artist, "album": album,
                "track_number": track_no if album else None, "release_year": year, "track": title}
        return {"rel": rel, "section": "Music", "tags": tags, "mixed_playlist": in_playlist and not album_like,
                "collection_title": album, "collection_url": info.get("playlist_webpage_url") if album_like else None}

    if audio:
        # audio is music: a song YouTube knows nothing about goes to its artist's singles,
        # read from an 'Artist - Song' title, else the channel (from a playlist, the
        # playlist itself becomes one of your playlists, see _run_job)
        channel = _clean_channel(info.get("uploader") or info.get("channel")) or "Unknown"
        parts = re.split(r"\s+[-–—]\s+", title, maxsplit=1)
        if len(parts) == 2 and 0 < len(parts[0]) <= 40 and parts[1].strip():
            channel, title = parts[0].strip(), _track_title(parts[1], None)
        adir = _artist_dir(channel)
        return {"rel": f"Music/{adir}/{SINGLES}/{_clean(title)}", "section": "Music",
                "tags": {"artist": channel, "album_artist": adir, "track": title}, "mixed_playlist": in_playlist,
                "collection_title": None, "collection_url": None}

    if in_playlist:
        pl = info.get("playlist_title") or "Playlist"
        idx = info.get("playlist_index")
        name = f"{int(idx):03d} - {_clean(title)}" if idx else _clean(title)
        return {"rel": f"Playlists/{_clean(pl)}/{name}", "section": "Playlists", "tags": tags,
                "collection_title": pl, "collection_url": info.get("playlist_webpage_url")}

    channel = _clean_channel(info.get("uploader") or info.get("channel"))
    return {"rel": f"Videos/{_clean(channel)}/{_clean(title)}", "section": "Videos", "tags": tags,
            "collection_title": channel, "collection_url": info.get("channel_url") or info.get("uploader_url")}
# "Primus- To Defy", "PRIMUS: Spaghetti Western", "Malloy - Primus" (not "Skinny-Constantinople")
_SEP = re.compile(r"\s+[-–—|:~]+\s*|\s*[-–—|:~]+\s+")
_FILE_EXT = re.compile(r"\.(?:wmv|avi|mp4|flv|mov|mkv|mp3|m4a|wav)\b", re.I)
_TRACK_NUM = re.compile(r"^\s*(?:track\s*)?\d{1,2}\s*[.)\-–]\s+|\s*#\d{1,2}\s*$", re.I)


def _norm(s: str) -> str:
    """Loose comparison form: 'Hello Skinny / Constantinople' == 'hello skinny-constantinople'."""
    import unicodedata
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold().replace("&", " and ")
    s = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", s)
    return re.sub(r"[^\w]+", " ", s).strip()


def _local_title(title: str, artist: str | None, album: str | None) -> str:
    """'Primus- Mr. Knowitall- Frizzle Fry' → 'Mr. Knowitall'."""
    drop = {_norm(x) for x in (artist, album) if x}
    parts = [p.strip() for p in _SEP.split(title) if p.strip()]
    keep = [p for p in parts if _norm(p) not in drop] or parts
    t = " - ".join(keep)
    t = _TRACK_NUM.sub("", _FILE_EXT.sub("", t))
    return _track_title(t, None)
