"""The HTTP routes that change things or take input from anyone who can reach
the site: uploads, progress, playlists, deleting and renaming, downloads."""
import io
import json
import subprocess

import pytest

from tubegrab import config, images, index, jobs, meta
from conftest import LIB, add_file, patch, reindex


def image(fmt: str = "png", size: str = "320x180") -> bytes:
    """A real picture made by ffmpeg."""
    return subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"color=red:s={size}",
                           "-frames:v", "1", "-f", {"png": "image2pipe", "jpg": "image2pipe"}[fmt],
                           "-c:v", {"png": "png", "jpg": "mjpeg"}[fmt], "pipe:1"],
                          capture_output=True, check=True).stdout


def size_of(path) -> tuple[int, int]:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout.strip()
    w, h = out.split(",")
    return int(w), int(h)


def upload(client, key, data: bytes, name="pic.png"):
    return client.post("/api/collections/cover/upload", content_type="multipart/form-data",
                       data={"key": key, "file": (io.BytesIO(data), name)})


# ---------------------------------------------------------------- pictures

def test_upload_album_picture_is_cropped_square(client):
    add_file("Music/Primus/Frizzle Fry (1990)/01 - To Defy.m4a", "frizzle0001", title="To Defy")
    r = upload(client, "Music/Primus/Frizzle Fry (1990)", image("png"))
    assert r.status_code == 200, r.get_json()
    cover = meta._folders()["Music/Primus/Frizzle Fry (1990)"]["cover_file"]
    w, h = size_of(config.COVERS_DIR / cover)
    assert w == h == 180


def test_upload_playlist_picture_is_wide(client):
    add_file("Playlists/Course/001 - Week 1.mp4", "week0000001", title="Week 1")
    assert upload(client, "Playlists/Course", image("jpg", "400x400"), "p.jpg").status_code == 200
    w, h = size_of(config.COVERS_DIR / meta._folders()["Playlists/Course"]["cover_file"])
    assert (w, h) == (400, 224)   # 16:9, height rounded to even


HLS_READ_LOCAL_FILE = (b"#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:10.0,\nfile:///etc/passwd\n#EXT-X-ENDLIST\n")


@pytest.mark.parametrize("data, name", [
    (HLS_READ_LOCAL_FILE, "cover.jpg"),           # a playlist that reads another file, named like a picture
    (b"<html>not a picture</html>", "x.png"),
    (b"", "empty.jpg"),
    (b"\xff\xd8\xff garbage that only starts like a JPEG", "broken.jpg"),
])
def test_upload_refuses_anything_but_a_picture(client, data, name, monkeypatch):
    add_file("Playlists/Course/001 - Week 1.mp4", "week0000001", title="Week 1")
    r = upload(client, "Playlists/Course", data, name)
    assert r.status_code == 400
    assert "cover_file" not in meta._folders().get("Playlists/Course", {})
    assert not list(config.COVERS_DIR.iterdir())


def test_playlist_is_never_given_to_ffmpeg(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(images.subprocess, "run", lambda *a, **k: calls.append(a))

    class Up:
        def save(self, dest):
            open(dest, "wb").write(HLS_READ_LOCAL_FILE)

    assert images._save_picture(Up(), tmp_path / "out.jpg") is False
    assert calls == []


@pytest.mark.parametrize("head, want", [
    (b"\xff\xd8\xff\xe0", "jpeg_pipe"), (b"\x89PNG\r\n\x1a\n....", "png_pipe"), (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "webp_pipe"),
    (b"GIF89a", "gif"), (b"BM\x00\x00", "bmp_pipe"), (b"#EXTM3U", None), (b"ffconcat version 1.0", None), (b"", None),
])
def test_image_demuxer(head, want):
    assert images._image_demuxer(head) == want


def test_upload_needs_a_collection(client):
    assert upload(client, "../..", image()).status_code == 404
    assert upload(client, "Videos/Nothing", image()).status_code == 404


# ---------------------------------------------------------------- progress

def test_progress_round_trip(client):
    add_file("Videos/Uni/Week 1.mp4", "week0000001", title="Week 1")
    reindex()
    assert client.post("/api/progress", json={"name": "Videos/Uni/Week 1.mp4", "pos": 61.26}).status_code == 200
    assert json.loads(client.get("/api/progress").data) == {"Videos/Uni/Week 1.mp4": {"pos": 61.3, "watched": False}}
    client.post("/api/progress", json={"name": "Videos/Uni/Week 1.mp4", "watched": True})
    assert json.loads(client.get("/api/progress").data) == {"Videos/Uni/Week 1.mp4": {"pos": 0.0, "watched": True}}


def test_progress_from_send_beacon(client):
    add_file("Videos/Uni/Week 1.mp4", "week0000001", title="Week 1")
    r = client.post("/api/progress", data=json.dumps({"name": "Videos/Uni/Week 1.mp4", "pos": 5}), content_type="text/plain")
    assert r.status_code == 200


@pytest.mark.parametrize("pos", ["Infinity", "-Infinity", "NaN", "1e999", '"1e999"', '"soon"', "null", "[1]"])
def test_progress_refuses_bad_positions(client, pos):
    add_file("Videos/Uni/Week 1.mp4", "week0000001", title="Week 1")
    body = '{"name": "Videos/Uni/Week 1.mp4", "pos": %s}' % pos   # as a client could send it
    assert client.post("/api/progress", data=body, content_type="application/json").status_code == 400
    json.loads(config.PROGRESS_FILE.read_text() if config.PROGRESS_FILE.exists() else "{}", parse_constant=pytest.fail)


def test_progress_needs_a_library_file(client):
    assert client.post("/api/progress", json={"name": "../../etc/passwd", "pos": 1}).status_code == 404


# ---------------------------------------------------------------- media ranges

@pytest.mark.parametrize("rng, status, body", [
    (None, 200, b"0123456789"),
    ("bytes=0-", 206, b"0123456789"),
    ("bytes=3-5", 206, b"345"),
    ("bytes=7-100", 206, b"789"),
    ("bytes=-3", 206, b"789"),
    ("bytes=-30", 206, b"0123456789"),
    ("bytes=10-", 416, b""),
    ("bytes=5-2", 416, b""),
    ("bytes=0-1,4-5", 200, b"0123456789"),   # several ranges: the whole file
])
def test_media_ranges(client, rng, status, body):
    add_file("Videos/Uni/Week 1.mp4").write_bytes(b"0123456789")
    r = client.get("/media/Videos/Uni/Week%201.mp4", headers={"Range": rng} if rng else {})
    assert r.status_code == status
    assert r.data == body
    if status == 206:
        assert r.headers["Content-Range"].endswith("/10")
    if status == 416:
        assert r.headers["Content-Range"] == "bytes */10"


def test_m4a_is_served_as_audio(client):
    add_file("Music/Primus/Singles/Song.m4a").write_bytes(b"x")
    assert client.get("/media/Music/Primus/Singles/Song.m4a").mimetype == "audio/mp4"


# ---------------------------------------------------------------- your playlists

def test_playlist_lifecycle_never_touches_files(client):
    for i in (1, 2, 3):
        add_file(f"Music/Primus/Singles/Song {i}.m4a", f"song{i:07d}", title=f"Song {i}")
    reindex()
    names = [f"Music/Primus/Singles/Song {i}.m4a" for i in (1, 2, 3)]
    p = client.post("/api/playlists", json={"title": "Road trip", "items": names}).get_json()["playlist"]
    assert p["items"] == names
    r = client.patch(f"/api/playlists/{p['id']}", json={"title": "Drive", "items": names[::-1]})
    assert r.get_json()["playlist"]["title"] == "Drive"
    assert r.get_json()["playlist"]["items"] == names[::-1]
    assert client.delete(f"/api/playlists/{p['id']}").status_code == 200
    assert client.get(f"/api/playlists/{p['id']}").status_code == 404
    assert all((LIB / n).exists() for n in names)


def test_playlist_items_must_be_in_the_library(client):
    r = client.post("/api/playlists", json={"title": "X", "items": ["Music/Nobody/Singles/Nothing.m4a"]})
    assert r.status_code == 400


def test_playlist_follows_a_moved_song(client):
    add_file("Music/Uni/Singles/Lecture.m4a", "lecture0001", title="Lecture", uploader="Uni")
    reindex()
    p = client.post("/api/playlists", json={"title": "Talks", "items": ["Music/Uni/Singles/Lecture.m4a"]}).get_json()["playlist"]
    client.post("/api/move", json={"name": "Music/Uni/Singles/Lecture.m4a", "to": "Videos"})
    reindex()
    assert client.get(f"/api/playlists/{p['id']}").get_json()["playlist"]["items"] == ["Videos/Uni/Lecture.m4a"]


# ---------------------------------------------------------------- deleting

def test_delete_album_removes_files_and_their_metadata(client):
    add_file("Music/Primus/Frizzle Fry (1990)/01 - To Defy.m4a", "frizzle0001", title="To Defy")
    add_file("Music/Primus/Singles/Other.m4a", "other000001", title="Other")
    r = client.post("/api/collections/delete", json={"key": "Music/Primus/Frizzle Fry (1990)"})
    assert r.get_json() == {"ok": True, "deleted": 1}
    assert not (LIB / "Music/Primus/Frizzle Fry (1990)").exists()
    assert not (config.META_DIR / "frizzle0001.json").exists()
    assert (LIB / "Music/Primus/Singles/Other.m4a").exists()
    assert "frizzle0001:audio" not in meta._index()


@pytest.mark.parametrize("key", ["Music/Primus", "Music", "Videos", "", "../..", ".meta"])
def test_delete_refuses_whole_artists_and_sections(client, key):
    add_file("Music/Primus/Singles/Other.m4a", "other000001", title="Other")
    add_file("Videos/Uni/Week 1.mp4", "week0000001", title="Week 1")
    assert client.post("/api/collections/delete", json={"key": key}).status_code in (400, 404)
    assert (LIB / "Music/Primus/Singles/Other.m4a").exists()


def test_delete_waits_for_downloads(client):
    add_file("Videos/Uni/Week 1.mp4", "week0000001", title="Week 1")
    config._jobs["j1"] = {"id": "j1", "status": "downloading"}
    assert client.post("/api/collections/delete", json={"key": "Videos/Uni"}).status_code == 409
    assert (LIB / "Videos/Uni/Week 1.mp4").exists()


def test_delete_one_file_keeps_metadata_of_its_other_copy(client):
    add_file("Videos/Uni/Week 1.mp4", "week0000001", title="Week 1")
    add_file("Music/Uni/Singles/Week 1.m4a", "week0000001", title="Week 1")
    client.post("/api/files/delete", json={"name": "Videos/Uni/Week 1.mp4"})
    assert (config.META_DIR / "week0000001.json").exists()
    client.post("/api/files/delete", json={"name": "Music/Uni/Singles/Week 1.m4a"})
    assert not (config.META_DIR / "week0000001.json").exists()


# ---------------------------------------------------------------- editing

def test_edit_album_name_and_year(client):
    add_file("Music/Primus/Frizzle Fry/01 - To Defy.m4a", "frizzle0001", title="To Defy")
    r = client.post("/api/collections/edit", json={"key": "Music/Primus/Frizzle Fry", "title": "Frizzle Fry",
                                                   "artist": "Primus", "year": "1990"})
    assert r.get_json() == {"ok": True, "key": "Music/Primus/Frizzle Fry (1990)"}
    assert meta._index()["frizzle0001:audio"] == "Music/Primus/Frizzle Fry (1990)/01 - To Defy.m4a"
    assert meta._meta_for("frizzle0001")["release_year"] == "1990"


def test_edit_fixing_an_artist_merges_into_the_right_one(client):
    add_file("Music/Primus/Frizzle Fry (1990)/01 - To Defy.m4a", "frizzle0001", title="To Defy")
    add_file("Music/Primsu/Frizzle Fry (1990)/02 - Ground Hog's Day.m4a", "frizzle0002", title="Ground Hog's Day")
    r = client.post("/api/collections/edit", json={"key": "Music/Primsu/Frizzle Fry (1990)", "title": "Frizzle Fry",
                                                   "artist": "Primus", "year": "1990"})
    assert r.get_json()["key"] == "Music/Primus/Frizzle Fry (1990)"
    assert sorted(p.name for p in (LIB / "Music/Primus/Frizzle Fry (1990)").iterdir()) == \
        ["01 - To Defy.m4a", "02 - Ground Hog's Day.m4a"]
    assert not (LIB / "Music/Primsu").exists()


def test_edit_never_overwrites(client):
    add_file("Music/Primus/Frizzle Fry (1990)/01 - To Defy.m4a", "frizzle0001", title="To Defy")
    add_file("Music/Primsu/Frizzle Fry (1990)/01 - To Defy.m4a", "frizzle9999", title="To Defy")
    r = client.post("/api/collections/edit", json={"key": "Music/Primsu/Frizzle Fry (1990)", "title": "Frizzle Fry",
                                                   "artist": "Primus", "year": "1990"})
    assert r.status_code == 409
    assert (LIB / "Music/Primsu/Frizzle Fry (1990)/01 - To Defy.m4a").exists()


@pytest.mark.parametrize("body, status", [
    ({"key": "Music/Primus/Frizzle Fry", "title": "Frizzle Fry", "year": "90"}, 400),
    ({"key": "Videos/Uni", "title": ""}, 400),
    ({"key": "Videos/Nobody", "title": "X"}, 404),
])
def test_edit_refuses(client, body, status):
    add_file("Music/Primus/Frizzle Fry/01 - To Defy.m4a", "frizzle0001", title="To Defy")
    add_file("Videos/Uni/Week 1.mp4", "week0000001", title="Week 1")
    assert client.post("/api/collections/edit", json=body).status_code == status


def test_edit_names_are_made_safe(client):
    add_file("Videos/Uni/Week 1.mp4", "week0000001", title="Week 1")
    r = client.post("/api/collections/edit", json={"key": "Videos/Uni", "title": "../../etc"})
    assert r.get_json()["key"] == "Videos/-..-etc"   # no "/", no leading dots
    assert (LIB / "Videos/-..-etc/Week 1.mp4").exists()


# ---------------------------------------------------------------- downloads

@pytest.mark.parametrize("body, status", [
    ({}, 400), ({"url": "  "}, 400), ({"url": "ftp://example.com/x"}, 400), ({"url": "file:///etc/passwd"}, 400),
    ({"url": "https://youtu.be/x", "format": "8k"}, 400),
])
def test_download_refuses_bad_requests(client, body, status, monkeypatch):
    monkeypatch.setattr("tubegrab.api.downloads._run_job", lambda *a: pytest.fail("started a download"))
    assert client.post("/api/download", json=body).status_code == status


def test_download_queues_a_job(client, monkeypatch):
    started = []
    monkeypatch.setattr("tubegrab.api.downloads._run_job", lambda *a: started.append(a))
    job = client.post("/api/download", json={"url": "https://youtu.be/abc", "format": "1080p"}).get_json()["job"]
    assert (job["status"], job["format"]) == ("queued", "video")   # old quality names mean video
    assert "cancel" not in job
    assert client.get("/api/jobs").get_json()["jobs"][0]["id"] == job["id"]
    assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 200
    assert config._jobs[job["id"]]["cancel"] is True
    assert client.post("/api/jobs/nope/cancel").status_code == 404


def test_job_history_is_capped():
    for i in range(80):
        config._jobs[f"done{i:02d}"] = {"id": f"done{i:02d}", "status": "complete", "finished_at": f"2026-01-01T00:{i:02d}"}
    config._jobs["live"] = {"id": "live", "status": "downloading", "finished_at": None}
    jobs._forget_old_jobs()
    assert len(config._jobs) == jobs.KEEP_FINISHED + 1
    assert "live" in config._jobs and "done79" in config._jobs and "done00" not in config._jobs


# ---------------------------------------------------------------- already downloaded?

def test_find_existing_uses_the_index(lib):
    add_file("Videos/Uni/Week 1.mp4", "week0000001", title="Week 1")
    assert index._find_existing("week0000001", "video") == lib / "Videos/Uni/Week 1.mp4"
    assert index._find_existing("week0000001", "audio") is None


def test_find_existing_legacy_name_without_walking_the_library(lib, monkeypatch):
    add_file("Videos/Uni/Old Name [dQw4w9WgXcQ].mp4")
    reindex()
    index._indexer.ready.set()
    monkeypatch.setattr(index, "_iter_media", lambda: pytest.fail("walked the library"))
    assert index._find_existing("dQw4w9WgXcQ", "video") == lib / "Videos/Uni/Old Name [dQw4w9WgXcQ].mp4"
    assert index._find_existing("xxxxxxxxxxx", "video") is None


def test_find_existing_before_the_first_index_pass(lib):
    add_file("Videos/Uni/Old Name [dQw4w9WgXcQ].mp4")
    assert index._find_existing("dQw4w9WgXcQ", "video") == lib / "Videos/Uni/Old Name [dQw4w9WgXcQ].mp4"


# ---------------------------------------------------------------- search

def test_search_finds_words_spoken_in_videos(client):
    add_file("Videos/Uni/Week 1.mp4", "week0000001", title="Week 1")
    add_file("Videos/Uni/Week 2.mp4", "week0000002", title="Week 2")
    (config.META_DIR / "week0000001.en.vtt").write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\ntoday we talk about the bass clef\n\n"
        "00:01:00.000 --> 00:01:02.000\nback to the bass clef again\n")
    reindex()
    res = client.get("/api/search?q=bass clef").get_json()["results"]
    assert [r["name"] for r in res] == ["Videos/Uni/Week 1.mp4"]
    assert res[0]["count"] == 2
    assert res[0]["hits"][0]["t"] == 1.0
    assert "today we talk about the bass clef" in res[0]["hits"][0]["text"]
    assert client.get("/api/search?q=ba").get_json() == {"results": []}
    assert client.get('/api/search?q=" OR *').get_json() == {"results": []}


def test_a_job_keeps_the_error_of_a_track_that_failed(monkeypatch):
    """ignoreerrors lets an album finish without the tracks YouTube refused;
    the job must still say why, or bin/grab --slow can't tell it was cut short."""

    class FakeDL:
        def __init__(self, opts, **kw):
            self.plans, self.last_error = {}, None

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def extract_info(self, url, download):
            self.last_error = "[youtube] xyz: This content isn't available, try again later."
            return {"_type": "playlist", "title": "Album", "entries": [{"id": "a"}, None]}

    monkeypatch.setattr(jobs, "_GrabDL", FakeDL)
    config._jobs["j1"] = {"id": "j1", "url": "https://x", "cancel": False, "files": []}
    jobs._run_job_slot("j1", "https://x", "video", True)
    job = config._jobs["j1"]
    assert job["status"] == "complete" and job["entry_count"] == 1
    assert "try again later" in job["error"]


def test_file_search_ignores_accents_case_and_punctuation(client):
    add_file("Music/Björk/Post (1995)/01 - Army of Me.m4a", "bjork000001", title="Army of Me", artist="Björk")
    add_file("Music/AC-DC/Back in Black (1980)/01 - Hells Bells.m4a", "acdc0000001", title="Hells Bells", artist="AC/DC")
    reindex()
    found = lambda q: [f["title"] for f in client.get(f"/api/files?q={q}").get_json()["files"]]
    assert found("bjork army") == ["Army of Me"]
    assert found("BJÖRK") == ["Army of Me"]
    assert found("acdc") == found("ac dc") == found("AC/DC bells") == ["Hells Bells"]


def test_songs_sort_by_artist_then_album_order(client):
    add_file("Music/Primus/Sailing (1991)/02 - B.m4a", "p0000000002", title="Zzz", artist="Primus", album="Sailing")
    add_file("Music/Primus/Sailing (1991)/01 - A.m4a", "p0000000001", title="Yyy", artist="Primus", album="Sailing")
    add_file("Music/Melvins/Houdini (1993)/01 - C.m4a", "m0000000001", title="Xxx", artist="Melvins", album="Houdini")
    reindex()
    names = [f["title"] for f in client.get("/api/files?section=Music&sort=artist").get_json()["files"]]
    assert names == ["Xxx", "Yyy", "Zzz"]


def test_an_old_index_gets_accent_free_search_without_a_rebuild(lib):
    from tubegrab import db
    add_file("Music/Björk/Post (1995)/01 - Army of Me.m4a", "bjork000001", title="Army of Me", artist="Björk")
    reindex()
    con = db._db()
    con.execute("UPDATE files SET search='army of me björk'")
    con.execute("PRAGMA user_version=2")
    db._db_init()
    assert con.execute("SELECT search FROM files").fetchone()[0].startswith("army of me bjork")
    assert con.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1   # kept, not dropped


# ---------------------------------------------------------------- favorites

def fav(client, kind, ident, on=True):
    return client.post("/api/favorites", json={"kind": kind, "id": ident, "on": on})


def test_favorites_heart_and_unheart_artists_albums_and_songs(client):
    add_file("Music/Primus/Frizzle Fry (1990)/01 - To Defy.m4a", "p0000000001", title="To Defy", artist="Primus")
    add_file("Music/Primus/Frizzle Fry (1990)/02 - Groundhog.m4a", "p0000000002", title="Groundhog", artist="Primus")
    reindex()
    assert fav(client, "artist", "Primus").status_code == 200
    assert fav(client, "album", "Music/Primus/Frizzle Fry (1990)").status_code == 200
    r = fav(client, "song", "Music/Primus/Frizzle Fry (1990)/02 - Groundhog.m4a").get_json()
    assert r == {"artists": ["Primus"], "albums": ["Music/Primus/Frizzle Fry (1990)"], "songs": ["p0000000002:audio"]}
    assert client.get("/api/favorites").get_json() == r
    reindex()
    songs = client.get("/api/files?section=Music&fav=1").get_json()
    assert [f["title"] for f in songs["files"]] == ["Groundhog"] and songs["total"] == 1

    fav(client, "album", "Music/Primus/Frizzle Fry (1990)", on=False)
    r = fav(client, "artist", "Primus", on=False).get_json()
    assert r["artists"] == [] and r["albums"] == []
    assert json.loads((config.FOLDERS_FILE).read_text() or "{}") == {}   # no empty record left behind
    fav(client, "song", "Music/Primus/Frizzle Fry (1990)/02 - Groundhog.m4a", on=False)
    reindex()
    assert client.get("/api/files?fav=1").get_json()["total"] == 0


@pytest.mark.parametrize("kind, ident", [
    ("album", "../outside"), ("album", "Videos/Uni"), ("album", "Music/Nobody"), ("song", "../../etc/passwd"),
    ("song", "Music/missing.m4a"), ("planet", "x"), ("artist", ""), ("artist", 5), ("artist", "x" * 600),
])
def test_favorites_refuse_what_isnt_there(client, kind, ident):
    (LIB / "Videos/Uni").mkdir(parents=True)
    assert fav(client, kind, ident).status_code == 400


def test_a_hearted_album_stays_hearted_when_renamed(client):
    add_file("Music/Primus/Frizzle Fry (1990)/01 - To Defy.m4a", "p0000000001", title="To Defy", artist="Primus")
    reindex()
    fav(client, "album", "Music/Primus/Frizzle Fry (1990)")
    r = client.post("/api/collections/edit", json={"key": "Music/Primus/Frizzle Fry (1990)", "title": "Frizzle Fry Deluxe"})
    assert r.status_code == 200, r.get_json()
    assert client.get("/api/favorites").get_json()["albums"] == [r.get_json()["key"]]


def test_a_hearted_song_stays_hearted_when_moved(client):
    add_file("Music/Primus/Frizzle Fry (1990)/01 - To Defy.m4a", "p0000000001", title="To Defy", artist="Primus")
    reindex()
    fav(client, "song", "Music/Primus/Frizzle Fry (1990)/01 - To Defy.m4a")
    r = client.post("/api/move", json={"name": "Music/Primus/Frizzle Fry (1990)/01 - To Defy.m4a", "to": "Videos"})
    assert r.status_code == 200
    reindex()
    assert [f["name"] for f in client.get("/api/files?fav=1").get_json()["files"]] == [r.get_json()["name"]]


# ---------------------------------------------------------------- plays

def test_plays_count_up_and_sort(client):
    a = add_file("Music/Primus/Frizzle Fry (1990)/01 - To Defy.m4a", "p0000000001", title="To Defy", artist="Primus")
    add_file("Music/Primus/Frizzle Fry (1990)/02 - Groundhog.m4a", "p0000000002", title="Groundhog", artist="Primus")
    reindex()
    for _ in range(2):
        assert client.post("/api/played", json={"name": "Music/Primus/Frizzle Fry (1990)/02 - Groundhog.m4a"}).status_code == 200
    assert client.post("/api/played", json={"name": "Music/Primus/Frizzle Fry (1990)/01 - To Defy.m4a"}).get_json() == {"plays": 1}
    files = client.get("/api/files?section=Music&sort=plays").get_json()["files"]
    assert [(f["title"], f["plays"]) for f in files] == [("Groundhog", 2), ("To Defy", 1)]
    assert files[0]["last_played"]
    assert client.get("/api/files?sort=played").get_json()["files"][0]["title"] == "To Defy"   # most recent
    assert json.loads(config.PLAYS_FILE.read_text())["p0000000002:audio"]["n"] == 2
    assert a.exists()


def test_plays_survive_an_index_rebuild(client):
    from tubegrab import db
    add_file("Music/Primus/Frizzle Fry (1990)/01 - To Defy.m4a", "p0000000001", title="To Defy", artist="Primus")
    reindex()
    client.post("/api/played", json={"name": "Music/Primus/Frizzle Fry (1990)/01 - To Defy.m4a"})
    db._db().execute("PRAGMA user_version=1")   # an old index: dropped and rebuilt from the files
    db._db_init()
    index._indexer.known = {}                     # (as after a restart)
    reindex()
    assert client.get("/api/files").get_json()["files"][0]["plays"] == 1


@pytest.mark.parametrize("body", [{"name": "../../etc/passwd"}, {"name": "Music/missing.m4a"}, {"name": 5}, {}])
def test_plays_refuse_what_isnt_there(client, body):
    assert client.post("/api/played", json=body).status_code == 404
    assert not config.PLAYS_FILE.exists()


# ---------------------------------------------------------------- genres

class FakeMusicBrainz:
    """Answers searches and lookups for a few artists / release groups."""
    def __init__(self, groups=None, artists=None):
        self.groups, self.artists, self.asked = groups or {}, artists or {}, []

    def __call__(self, url, mb=False):
        import urllib.parse
        self.asked.append(url)
        u = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(u.query).get("query", [""])[0]
        if u.path.endswith("/release-group/"):
            hits = [{"id": f"rg:{k}", "score": 100} for k in self.groups if f'"{k}"' in q]
            return {"release-groups": hits}
        if u.path.endswith("/artist/"):
            return {"artists": [{"id": f"a:{k}", "score": 100} for k in self.artists if f'"{k}"' in q]}
        kind, _, name = u.path.rsplit("/", 1)[-1].partition(":")
        table = self.groups if kind == "rg" else self.artists
        return {"genres": [{"name": g, "count": 10 - i} for i, g in enumerate(table.get(name, []))] + [{"name": "unvoted", "count": 0}]}


def test_genres_come_from_the_album_or_else_the_artist(client, monkeypatch):
    from tubegrab import genres
    add_file("Music/Primus/Frizzle Fry (1990)/01 - To Defy.m4a", "p0000000001", title="To Defy", artist="Primus", album="Frizzle Fry")
    add_file("Music/Primus/Rare Thing (1999)/01 - X.m4a", "p0000000002", title="X", artist="Primus", album="Rare Thing")
    add_file("Music/Primus/Singles/Y.m4a", "p0000000003", title="Y", artist="Primus")
    reindex()
    mb = FakeMusicBrainz(groups={"Frizzle Fry": ["funk metal", "alternative metal"]}, artists={"Primus": ["funk metal", "alternative rock"]})
    patch(monkeypatch, "_http_json", mb)
    genres.backfill_genres()
    reindex()
    got = {c["key"]: c["genres"] for c in client.get("/api/library").get_json()["collections"]}
    assert got == {"Music/Primus/Frizzle Fry (1990)": ["funk metal", "alternative metal"],
                   "Music/Primus/Rare Thing (1999)": ["funk metal", "alternative rock"],   # (the artist's)
                   "Music/Primus/Singles": ["funk metal", "alternative rock"]}
    assert sum("/artist/" in u for u in mb.asked) == 2   # the artist was looked up once (search + lookup)

    n = len(mb.asked)
    genres.backfill_genres()
    assert len(mb.asked) == n   # each folder is asked about once


def test_genres_wait_when_musicbrainz_is_unreachable(client, monkeypatch):
    from tubegrab import genres
    add_file("Music/Primus/Frizzle Fry (1990)/01 - To Defy.m4a", "p0000000001", title="To Defy", artist="Primus")
    reindex()
    genres.backfill_genres()   # (conftest: the network is off)
    assert "genres" not in json.loads(config.FOLDERS_FILE.read_text() if config.FOLDERS_FILE.exists() else "{}").get(
        "Music/Primus/Frizzle Fry (1990)", {})
    patch(monkeypatch, "_http_json", FakeMusicBrainz())
    genres.backfill_genres()
    assert json.loads(config.FOLDERS_FILE.read_text())["Music/Primus/Frizzle Fry (1990)"]["genres"] == []   # asked: none
