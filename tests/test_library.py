"""Library paths (the app is reachable from the internet: nothing outside the
library may be served), collection names, captions, lyrics."""
import os

import pytest

import app as tg
from conftest import add_file


# ---------------------------------------------------------------- path safety

@pytest.mark.parametrize("name", [
    "", "/etc/passwd", "../outside.mp4", "Music/../../outside.mp4", "Music/./a.mp4", "Music//a.mp4",
    ".meta/index.json", "Music/.hidden/a.m4a", "Music\\a.m4a", "Videos/",
])
def test_safe_path_refuses(name):
    assert tg._safe_path(name) is None


def test_safe_path_finds_library_files(lib):
    add_file("Videos/Uni/Week 1.mp4")
    assert tg._safe_path("Videos/Uni/Week 1.mp4") == lib / "Videos/Uni/Week 1.mp4"
    assert tg._safe_path("Videos/Uni/Missing.mp4") is None
    assert tg._safe_path("Videos/Uni") is None                     # a folder, asked for as a file
    assert tg._safe_path("Videos/Uni", dir_ok=True) == lib / "Videos/Uni"


def test_safe_path_refuses_symlinks_out_of_the_library(lib, tmp_path):
    secret = tmp_path / "secret.mp4"
    secret.write_text("x")
    (lib / "Videos").mkdir()
    os.symlink(secret, lib / "Videos/link.mp4")
    os.symlink(tmp_path, lib / "Videos/linkdir")
    assert tg._safe_path("Videos/link.mp4") is None
    assert tg._safe_path("Videos/linkdir", dir_ok=True) is None


def test_safe_path_refuses_the_library_root():
    assert tg._safe_path("Music/..", dir_ok=True) is None


@pytest.mark.parametrize("url", [
    "/media/..%2F..%2Fetc%2Fpasswd", "/media/.meta/index.json", "/thumb/..%2Fsecret",
    "/api/watch/..%2F..%2Fetc%2Fpasswd",
])
def test_routes_refuse_paths_outside_the_library(client, url):
    r = client.get(url)
    assert r.status_code in (400, 404)
    assert b"root:" not in r.data


def test_media_serves_a_library_file(client):
    path = add_file("Videos/Uni/Week 1.mp4")
    path.write_bytes(b"0123456789")
    r = client.get("/media/Videos/Uni/Week%201.mp4", headers={"Range": "bytes=2-5"})
    assert r.status_code == 206
    assert r.data == b"2345"


# ---------------------------------------------------------------- names

@pytest.mark.parametrize("name, want", [
    ("Music/Primus/Frizzle Fry (1990)/03 - Too Many Puppies.m4a", "Too Many Puppies"),
    ("Videos/Uni/Old Name [dQw4w9WgXcQ].mp4", "Old Name"),
    ("Playlists/Course/012 - Week 12.mp4", "Week 12"),
    ("Videos/Uni/1999 - A year.mp4", "1999 - A year"),
])
def test_display_title(name, want):
    assert tg._display_title(name) == want


@pytest.mark.parametrize("key, kind, title, extra", [
    ("Music/Primus/Frizzle Fry (1990)", "album", "Frizzle Fry", {"artist": "Primus", "year": "1990"}),
    ("Music/Primus/Live", "album", "Live", {"year": None}),
    ("Music/Primus/Singles", "singles", "Singles", {"artist": "Primus"}),
    ("Videos/Uni", "channel", "Uni", {"section": "Videos"}),
    ("Playlists/Course - Bass", "playlist", "Course - Bass", {"section": "Playlists"}),
    ("Other/Stuff", "folder", "Stuff", {"section": "Other"}),
])
def test_collection_info(key, kind, title, extra):
    c = tg._collection_info(key)
    assert (c["kind"], c["title"]) == (kind, title)
    for k, v in extra.items():
        assert c[k] == v


def test_unique_avoids_another_videos_file(lib):
    add_file("Videos/Uni/Intro.mp4", "aaaaaaaaaaa")
    assert tg._unique("Videos/Uni/Intro", ".mp4", "aaaaaaaaaaa") == "Videos/Uni/Intro"     # its own file
    assert tg._unique("Videos/Uni/Intro", ".mp4", "bbbbbbbbbbb") == "Videos/Uni/Intro (2)"
    assert tg._unique("Videos/Uni/Intro", ".m4a", "bbbbbbbbbbb") == "Videos/Uni/Intro"     # audio beside video is fine


def test_clean_chapters():
    assert tg._clean_chapters({"title": "T", "chapters": [{"title": "T", "start_time": 0}]}) is None
    assert tg._clean_chapters({"title": "T", "chapters": [{"title": "T", "start_time": 0},
                                                          {"title": "T", "start_time": 9}]}) is None
    assert tg._clean_chapters({"title": "T", "chapters": [{"title": "Intro", "start_time": 0},
                                                          {"title": "Part 1", "start_time": 61.234}]}) == [
        {"start": 0, "title": "Intro"}, {"start": 61.23, "title": "Part 1"}]


# ---------------------------------------------------------------- captions, lyrics

def test_ts():
    assert tg._ts("01:02:03.500") == 3723.5
    assert tg._ts("02:03,250") == 123.25
    assert tg._ts("nonsense") == 0.0


VTT = """WEBVTT
Kind: captions
Language: en

00:00:01.000 --> 00:00:03.000 align:start position:0%
hello<00:00:01.500><c> there</c>

00:00:03.000 --> 00:00:03.010
hello there

00:00:03.010 --> 00:00:05.000
hello there
general &amp; friends

00:00:20.000 --> 00:00:22.000
much later
"""


def test_parse_vtt_collapses_rolling_captions():
    assert tg._parse_vtt(VTT) == [
        {"start": 1.0, "end": 3.01, "text": "hello there"},
        {"start": 3.01, "end": 11.01, "text": "general & friends"},   # lasts until the next line, max 8 s
        {"start": 20.0, "end": 22.0, "text": "much later"},
    ]


def test_parse_lrc():
    text = "[ar:Primus]\n[00:01.50]First line\n[00:04.00][01:00.00]Chorus\n[00:06.00]\n[00:08.25]Last"
    assert tg._parse_lrc(text) == [
        {"start": 1.5, "end": 4.0, "text": "First line"},
        {"start": 4.0, "end": 6.0, "text": "Chorus"},
        {"start": 8.25, "end": 60.0, "text": "Last"},
        {"start": 60.0, "end": 66.0, "text": "Chorus"},
    ]
