#!/usr/bin/env python3
"""TubeGrab library and playlists from the command line, for agents and people.

Prints the music library as compact tab-separated tables (one line per song or
album), so a big library fits in a few thousand short lines, and creates or
changes playlists from song ids. Standard library only.

    tg.py stats                                   overview: genres, artists, plays, favorites
    tg.py albums [--genre G] [--artist A] [--years 1990-1999] [--fav]
    tg.py songs  [filters] [--sort S] [--limit N]  (tg.py songs -h for the filters)
    tg.py playlists                               your playlists
    tg.py show PLAYLIST_ID                        one playlist's songs
    tg.py create "Title" [-d "Description"] ID…   (ids as arguments, or one per line on stdin)
    tg.py add PLAYLIST_ID ID…                     append songs
    tg.py replace PLAYLIST_ID ID…                 new song list, in this order
    tg.py rename PLAYLIST_ID "Title" [-d "Description"]

A song's id is the first column of `songs` (its YouTube id, or its library path
for files without one). Server: $TUBEGRAB_URL, else the NAS on the LAN.
"""

import argparse
import json
import os
import random
import sys
import unicodedata
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone

SERVER = os.environ.get("TUBEGRAB_URL", "http://192.168.254.10:8765").rstrip("/")
PUBLIC = os.environ.get("TUBEGRAB_PUBLIC", "https://tube.putty-ai.com").rstrip("/")


def call(path, body=None, method=None):
    req = urllib.request.Request(SERVER + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method=method or ("GET" if body is None else "POST"))
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error")
        except ValueError:
            msg = None
        sys.exit(f"tg: {msg or f'HTTP {e.code}'} ({path})")
    except urllib.error.URLError as e:
        sys.exit(f"tg: can't reach {SERVER}: {e.reason}")


def fold(s):
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).casefold()


# ---------------------------------------------------------------- loading

def load():
    """Albums (with genres), every song (with plays, genres, favorites)."""
    lib = call("/api/library")
    favs = call("/api/favorites")
    albums = [c for c in lib["collections"] if c["section"] == "Music"]
    genres = {c["key"]: c.get("genres") or [] for c in albums}
    songs, offset = [], 0
    while True:
        page = call(f"/api/files?section=Music&sort=artist&limit=2000&offset={offset}")
        songs += page["files"]
        offset += len(page["files"])
        if not page["files"] or offset >= page["total"]:
            break
    fav_songs, fav_albums, fav_artists = set(favs["songs"]), set(favs["albums"]), set(favs["artists"])
    for f in songs:
        f["sid"] = f["id"] or f["name"]
        f["genres"] = genres.get(f["collection"], [])
        f["fav"] = (f"{f['id']}:{f['kind']}" if f["id"] else f"path:{f['name']}") in fav_songs
        f["fav_any"] = f["fav"] or f["collection"] in fav_albums or (f["artist"] or "") in fav_artists
    for c in albums:
        c["fav"] = c["key"] in fav_albums
    return albums, songs, favs


def years(spec):
    lo, _, hi = (spec or "").partition("-")
    return int(lo or 0), int(hi or lo or 9999)


def in_years(year, spec):
    if not spec:
        return True
    lo, hi = years(spec)
    return bool(year) and str(year)[:4].isdigit() and lo <= int(str(year)[:4]) <= hi


def any_in(needles, *hay):
    h = fold(" | ".join(x for x in hay if x))
    return any(fold(n) in h for n in needles)


def mmss(sec):
    sec = int(sec or 0)
    return f"{sec // 60}:{sec % 60:02d}"


def day(iso):
    return (iso or "")[:10]


def days_ago(iso):
    if not iso:
        return None
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).days
    except ValueError:
        return None


def table(header, rows, limit=None):
    print("\t".join(header))
    shown = rows if limit is None else rows[:limit]
    for r in shown:
        print("\t".join("" if v is None else str(v) for v in r))
    if len(shown) < len(rows):
        print(f"… {len(rows) - len(shown)} more (narrow the filters, or --limit)")


# ---------------------------------------------------------------- commands

def cmd_stats(a):
    albums, songs, favs = load()
    print(f"{len(songs)} songs · {len(albums)} albums/singles folders · "
          f"{len({c['artist'] for c in albums})} artists · {sum(f['duration'] or 0 for f in songs) / 3600:.0f} h")
    played = [f for f in songs if f["plays"]]
    print(f"played: {len(played)} songs, {sum(f['plays'] for f in songs)} plays in all")
    print(f"favorites: {len(favs['artists'])} artists · {len(favs['albums'])} albums · {len(favs['songs'])} songs")
    looked = sum(1 for c in albums if c.get("genres"))
    print(f"genres known for {looked} of {len(albums)} folders")
    g = Counter(x for f in songs for x in f["genres"])
    print("\ntop genres (songs):", ", ".join(f"{k} {n}" for k, n in g.most_common(40)) or "none yet")
    by_artist = Counter(f["artist"] or "?" for f in songs)
    print("\nbiggest artists (songs):", ", ".join(f"{k} {n}" for k, n in by_artist.most_common(30)))
    decades = Counter(f"{str(f['year'])[:3]}0s" for f in songs if str(f.get("year") or "")[:4].isdigit())
    print("\ndecades (songs):", ", ".join(f"{k} {n}" for k, n in sorted(decades.items())))
    if played:
        print("\nmost played:")
        for f in sorted(played, key=lambda f: -f["plays"])[:15]:
            print(f"  {f['plays']}×  {f['artist']} — {f['title']}")
    if favs["artists"]:
        print("\nfavorite artists:", ", ".join(favs["artists"]))
    pls = call("/api/playlists")["playlists"]
    if pls:
        print("\nplaylists:", ", ".join(f"{p['title']} ({p['count']})" for p in pls))


def cmd_albums(a):
    albums, _, _ = load()
    rows = [c for c in albums
            if (not a.genre or any_in(a.genre, *(c.get("genres") or [])))
            and (not a.artist or any_in(a.artist, c["artist"]))
            and in_years(c.get("year"), a.years) and (not a.fav or c["fav"])]
    rows.sort(key=lambda c: (fold(c["artist"] or ""), c.get("year") or ""))
    table(["artist", "album", "year", "tracks", "genres", "fav"],
          [(c["artist"], "Singles" if c["kind"] == "singles" else c["title"], c.get("year"), c["count"],
            ", ".join(c.get("genres") or []), "♥" if c["fav"] else "") for c in rows], a.limit)


def pick_songs(a, songs):
    out = []
    for f in songs:
        if a.genre and not any_in(a.genre, *f["genres"]):
            continue
        if a.not_genre and any_in(a.not_genre, *f["genres"]):
            continue
        if a.artist and not any_in(a.artist, f["artist"]):
            continue
        if a.not_artist and any_in(a.not_artist, f["artist"]):
            continue
        if a.album and not any_in(a.album, f["album"], f["collection"].split("/")[-1]):
            continue
        if a.q and not all(fold(w) in fold(" ".join(filter(None, (f["title"], f["artist"], f["album"])))) for w in a.q.split()):
            continue
        if not in_years(f.get("year"), a.years):
            continue
        if a.fav and not f["fav"]:
            continue
        if a.fav_any and not f["fav_any"]:
            continue
        if a.min_plays and f["plays"] < a.min_plays:
            continue
        if a.unplayed and f["plays"]:
            continue
        if a.not_since is not None:
            d = days_ago(f["last_played"])
            if d is not None and d < a.not_since:
                continue
        if a.min_minutes and (f["duration"] or 0) < a.min_minutes * 60:
            continue
        if a.max_minutes and (f["duration"] or 0) > a.max_minutes * 60:
            continue
        out.append(f)
    sort = a.sort
    if sort == "plays":
        out.sort(key=lambda f: -f["plays"])
    elif sort == "played":
        out.sort(key=lambda f: f["last_played"] or "", reverse=True)
    elif sort == "title":
        out.sort(key=lambda f: fold(f["title"]))
    elif sort == "year":
        out.sort(key=lambda f: (str(f.get("year") or "9999"), fold(f["artist"] or "")))
    elif sort == "random":
        random.Random(a.seed).shuffle(out)
    return out   # default: artist, album, track order (as loaded)


def cmd_songs(a):
    _, songs, _ = load()
    rows = pick_songs(a, songs)
    table(["id", "artist", "album", "year", "title", "length", "plays", "last played", "genres", "fav"],
          [(f["sid"], f["artist"], f["album"] or ("Singles" if f["collection"].endswith("/Singles") else ""),
            f.get("year"), f["title"], mmss(f["duration"]), f["plays"] or "", day(f["last_played"]),
            ", ".join(f["genres"]), "♥" if f["fav"] else ("·" if f["fav_any"] else "")) for f in rows], a.limit)
    if rows:
        print(f"({len(rows)} songs, {sum(f['duration'] or 0 for f in rows) / 60:.0f} min)")


def resolve(ids):
    """Song ids (first column of `songs`, or library paths) → library names, in order, once each."""
    if not ids or ids == ["-"]:
        ids = [line.split("\t")[0].strip() for line in sys.stdin if line.strip() and not line.startswith(("id\t", "…", "("))]
    _, songs, _ = load()
    by = {f["sid"]: f["name"] for f in songs} | {f["name"]: f["name"] for f in songs}
    names, missing = [], []
    for i in ids:
        n = by.get(i)
        if n is None:
            missing.append(i)
        elif n not in names:
            names.append(n)
    if missing:
        sys.exit(f"tg: not in the library: {', '.join(missing[:20])}{' …' if len(missing) > 20 else ''}")
    if not names:
        sys.exit("tg: no songs given")
    return names


def say_playlist(p):
    mins = (p.get("duration") or 0) / 60
    print(f"{p['title']} · {p['count']} songs · {mins:.0f} min")
    print(f"id {p['id']} · {PUBLIC}/p/{p['id']}")


def cmd_playlists(a):
    pls = call("/api/playlists")["playlists"]
    table(["id", "title", "songs", "minutes", "description"],
          [(p["id"], p["title"], p["count"], round((p["duration"] or 0) / 60), p["description"]) for p in pls])


def cmd_show(a):
    p = call(f"/api/playlists/{a.id}")["playlist"]
    say_playlist(p)
    if p.get("description"):
        print(p["description"])
    for n, f in enumerate(p.get("files") or [], 1):
        print(f"{n}\t{f['id'] or f['name']}\t{f['artist']}\t{f['title']}\t{mmss(f['duration'])}")


def cmd_create(a):
    p = call("/api/playlists", {"title": a.title, "description": a.description or "", "items": resolve(a.ids)})["playlist"]
    say_playlist(p)


def cmd_add(a):
    cur = call(f"/api/playlists/{a.id}")["playlist"]["items"]
    new = [n for n in resolve(a.ids) if n not in cur]
    say_playlist(call(f"/api/playlists/{a.id}", {"items": cur + new}, "PATCH")["playlist"])


def cmd_replace(a):
    say_playlist(call(f"/api/playlists/{a.id}", {"items": resolve(a.ids)}, "PATCH")["playlist"])


def cmd_rename(a):
    patch = {"title": a.title}
    if a.description is not None:
        patch["description"] = a.description
    say_playlist(call(f"/api/playlists/{a.id}", patch, "PATCH")["playlist"])


def main():
    ap = argparse.ArgumentParser(prog="tg.py", description="TubeGrab library and playlists.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats", help="overview").set_defaults(fn=cmd_stats)

    al = sub.add_parser("albums", help="albums and singles folders")
    al.add_argument("--genre", action="append", help="any genre containing this (repeat: any of them)")
    al.add_argument("--artist", action="append")
    al.add_argument("--years", help="1990-1999, or 1994")
    al.add_argument("--fav", action="store_true", help="hearted albums only")
    al.add_argument("--limit", type=int, default=400)
    al.set_defaults(fn=cmd_albums)

    so = sub.add_parser("songs", help="songs, filtered")
    so.add_argument("--genre", action="append", help="its album/artist has a genre containing this (repeat: any)")
    so.add_argument("--not-genre", action="append")
    so.add_argument("--artist", action="append", help="artist contains this (repeat: any)")
    so.add_argument("--not-artist", action="append")
    so.add_argument("--album", action="append")
    so.add_argument("--q", help="words in title / artist / album (all of them)")
    so.add_argument("--years", help="1990-1999, or 1994")
    so.add_argument("--fav", action="store_true", help="hearted songs")
    so.add_argument("--fav-any", action="store_true", help="hearted songs, or songs of hearted albums / artists")
    so.add_argument("--min-plays", type=int, default=0)
    so.add_argument("--unplayed", action="store_true", help="never played")
    so.add_argument("--not-since", type=int, metavar="DAYS", help="not played in the last DAYS days (or never)")
    so.add_argument("--min-minutes", type=float)
    so.add_argument("--max-minutes", type=float)
    so.add_argument("--sort", choices=["artist", "title", "year", "plays", "played", "random"], default="artist")
    so.add_argument("--seed", type=int)
    so.add_argument("--limit", type=int, default=300)
    so.set_defaults(fn=cmd_songs)

    sub.add_parser("playlists", help="your playlists").set_defaults(fn=cmd_playlists)
    sh = sub.add_parser("show", help="one playlist")
    sh.add_argument("id")
    sh.set_defaults(fn=cmd_show)
    cr = sub.add_parser("create", help="new playlist from song ids")
    cr.add_argument("title")
    cr.add_argument("-d", "--description")
    cr.add_argument("ids", nargs="*", help="song ids; none (or -) reads them from stdin")
    cr.set_defaults(fn=cmd_create)
    for name, fn, helptext in (("add", cmd_add, "append songs"), ("replace", cmd_replace, "new song list")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("id")
        p.add_argument("ids", nargs="*")
        p.set_defaults(fn=fn)
    rn = sub.add_parser("rename", help="new title / description")
    rn.add_argument("id")
    rn.add_argument("title")
    rn.add_argument("-d", "--description")
    rn.set_defaults(fn=cmd_rename)

    a, extra = ap.parse_known_args()   # (ids may come after -d too: create "T" -d "D" ID…)
    if extra and hasattr(a, "ids") and not any(x.startswith("-") and x != "-" for x in extra):
        a.ids += extra
    elif extra:
        ap.error(f"unrecognized arguments: {' '.join(extra)}")
    a.fn(a)


if __name__ == "__main__":
    main()
