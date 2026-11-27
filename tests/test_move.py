"""Moving files and collections between Music and the video side, and the
tidy-up that runs at startup leaving your moves alone."""
import json

from tubegrab import maintenance, meta
from conftest import add_file, reindex

PL = "PLfanfanfanfanfan"


def meta_of(vid):
    return meta._meta_for(vid)


def move(client, to, **target):
    r = client.post("/api/move", json={**target, "to": to})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def files(lib):
    return sorted(p.relative_to(lib).as_posix() for p in lib.rglob("*") if p.is_file() and ".meta" not in p.parts)


# ---------------------------------------------------------------- one file

def test_lecture_downloaded_as_audio_moves_to_videos_and_back(client, lib):
    add_file("Music/Uni/Singles/Lecture 3.m4a", "lecture0003", title="Lecture 3", uploader="Uni")
    assert move(client, "Videos", name="Music/Uni/Singles/Lecture 3.m4a") == {"name": "Videos/Uni/Lecture 3.m4a"}
    assert files(lib) == ["Videos/Uni/Lecture 3.m4a"]
    assert meta._index()["lecture0003:audio"] == "Videos/Uni/Lecture 3.m4a"
    assert meta_of("lecture0003")["placed"] == "user"
    assert meta_of("lecture0003")["section"] == "Videos"

    assert move(client, "Music", name="Videos/Uni/Lecture 3.m4a") == {"name": "Music/Uni/Singles/Lecture 3.m4a"}
    assert files(lib) == ["Music/Uni/Singles/Lecture 3.m4a"]


def test_album_track_goes_back_into_its_album(client, lib):
    (lib / "Music/Primus/Frizzle Fry (1990)").mkdir(parents=True)
    add_file("Videos/Primus/Too Many Puppies.m4a", "puppies0003", title="Too Many Puppies", track="Too Many Puppies",
             artist="Primus", album="Frizzle Fry")
    meta._write_meta({"id": "puppies0003"}, {"track_number": 3})
    assert move(client, "Music", name="Videos/Primus/Too Many Puppies.m4a")["name"] == \
        "Music/Primus/Frizzle Fry (1990)/03 - Too Many Puppies.m4a"


def test_moving_to_where_it_already_is_does_nothing(client, lib):
    add_file("Music/Uni/Singles/Lecture 3.m4a", "lecture0003", title="Lecture 3", uploader="Uni")
    assert move(client, "Music", name="Music/Uni/Singles/Lecture 3.m4a") == {"name": "Music/Uni/Singles/Lecture 3.m4a"}
    assert "placed" not in meta_of("lecture0003")


def test_move_does_not_overwrite_another_file(client, lib):
    add_file("Videos/Uni/Lecture 3.mp4", "othervideo1", title="Lecture 3", uploader="Uni")
    add_file("Music/Uni/Singles/Lecture 3.m4a", "lecture0003", title="Lecture 3", uploader="Uni")
    move(client, "Videos", name="Music/Uni/Singles/Lecture 3.m4a")
    assert files(lib) == ["Videos/Uni/Lecture 3.m4a", "Videos/Uni/Lecture 3.mp4"]
    add_file("Videos/Uni/Lecture 4.m4a", "othervideo2", title="Lecture 4", uploader="Uni")
    add_file("Music/Uni/Singles/Lecture 4.m4a", "lecture0004", title="Lecture 4", uploader="Uni")
    assert move(client, "Videos", name="Music/Uni/Singles/Lecture 4.m4a")["name"] == "Videos/Uni/Lecture 4 (2).m4a"


def test_move_refuses_bad_requests(client):
    assert client.post("/api/move", json={"name": "x", "to": "Elsewhere"}).status_code == 400
    assert client.post("/api/move", json={"name": "../../etc/passwd", "to": "Music"}).status_code == 404
    assert client.post("/api/move", json={"key": "../..", "to": "Music"}).status_code == 404
    assert client.post("/api/move", json={"key": ".meta", "to": "Music"}).status_code == 404


# ---------------------------------------------------------------- whole collections

def test_album_to_videos_becomes_a_playlist_in_album_order(client, lib):
    for n, t in ((2, "Ground Hog's Day"), (1, "To Defy the Laws of Tradition"), (3, "Too Many Puppies")):
        add_file(f"Music/Primus/Frizzle Fry (1990)/{n:02d} - {t}.m4a", f"frizzle{n:04d}", title=t, uploader="Fan")
    meta._note_folder("Music/Primus/Frizzle Fry (1990)", "Frizzle Fry", "https://example.com/pl")
    assert move(client, "Videos", key="Music/Primus/Frizzle Fry (1990)") == {"key": "Playlists/Frizzle Fry"}
    assert files(lib) == ["Playlists/Frizzle Fry/001 - To Defy the Laws of Tradition.m4a",
                          "Playlists/Frizzle Fry/002 - Ground Hog's Day.m4a",
                          "Playlists/Frizzle Fry/003 - Too Many Puppies.m4a"]
    assert meta._folders()["Playlists/Frizzle Fry"]["url"] == "https://example.com/pl"
    assert "Music/Primus/Frizzle Fry (1990)" not in meta._folders()


def test_album_to_videos_does_not_merge_into_an_existing_playlist(client, lib):
    add_file("Playlists/Frizzle Fry/001 - Something else.mp4", "somethingel", title="Something else")
    add_file("Music/Primus/Frizzle Fry (1990)/01 - To Defy.m4a", "frizzle0001", title="To Defy")
    assert move(client, "Videos", key="Music/Primus/Frizzle Fry (1990)") == {"key": "Playlists/Frizzle Fry (2)"}


def test_singles_to_videos_go_to_their_channels(client, lib):
    add_file("Music/Uni/Singles/Lecture 1.m4a", "lecture0001", title="Lecture 1", uploader="Uni")
    add_file("Music/Uni/Singles/Lecture 2.m4a", "lecture0002", title="Lecture 2", uploader="Uni")
    assert move(client, "Videos", key="Music/Uni/Singles") == {"key": "Videos/Uni"}
    assert files(lib) == ["Videos/Uni/Lecture 1.m4a", "Videos/Uni/Lecture 2.m4a"]


def test_channel_to_music_becomes_singles(client, lib):
    add_file("Videos/Primus/Primus - Tommy the Cat (Official Video).mp4", "tommycat001",
             title="Primus - Tommy the Cat (Official Video)", uploader="Primus")
    assert move(client, "Music", key="Videos/Primus") == {"key": "Music/Primus/Singles"}
    assert files(lib) == ["Music/Primus/Singles/Tommy the Cat.mp4"]


def test_fan_playlist_to_music_becomes_its_album(client, lib):
    titles = ["Primus- To Defy The Laws Of Tradition", "Primus - Ground Hog's Day", "PRIMUS: Too Many Puppies"]
    for i, t in enumerate(titles, 1):
        add_file(f"Playlists/Primus - Frizzle Fry (Full Album)/{i:03d} - {t}.m4a", f"fan{i:08d}", title=t,
                 uploader="Fan", playlist_id=PL, playlist_index=i)
    meta._note_folder("Playlists/Primus - Frizzle Fry (Full Album)", "Primus - Frizzle Fry (Full Album)", None)
    key = move(client, "Music", key="Playlists/Primus - Frizzle Fry (Full Album)")["key"]
    assert key == "Music/Primus/Frizzle Fry"
    assert files(lib) == ["Music/Primus/Frizzle Fry/01 - To Defy The Laws Of Tradition.m4a",
                          "Music/Primus/Frizzle Fry/02 - Ground Hog's Day.m4a",
                          "Music/Primus/Frizzle Fry/03 - Too Many Puppies.m4a"]
    assert meta_of("fan00000002")["album"] == "Frizzle Fry"
    assert meta_of("fan00000002")["track_number"] == 2


def test_playlist_to_music_named_after_the_playlist(client, lib):
    for i, t in enumerate(["Week 1", "Week 2"], 1):
        add_file(f"Playlists/Bass course/{i:03d} - {t}.m4a", f"week{i:07d}", title=t, uploader="Uni",
                 playlist_id=PL, playlist_index=i)
    assert move(client, "Music", key="Playlists/Bass course") == {"key": "Music/Uni/Bass course"}
    assert files(lib) == ["Music/Uni/Bass course/01 - Week 1.m4a", "Music/Uni/Bass course/02 - Week 2.m4a"]


# ---------------------------------------------------------------- tidy leaves your moves alone

def test_tidy_does_not_move_your_playlist_back_into_music(client, lib):
    titles = ["Primus- To Defy The Laws Of Tradition", "Primus - Ground Hog's Day", "PRIMUS: Too Many Puppies"]
    for i, t in enumerate(titles, 1):
        add_file(f"Music/Primus/Frizzle Fry/{i:02d} - {t}.m4a", f"fan{i:08d}", title=t, uploader="Fan",
                 playlist_id=PL, playlist_index=i, playlist_title="Primus - Frizzle Fry (Full Album)")
    move(client, "Videos", key="Music/Primus/Frizzle Fry")
    before = files(lib)
    assert all(f.startswith("Playlists/") for f in before)
    maintenance.tidy_music()
    assert files(lib) == before


def test_tidy_refiles_old_playlist_songs_into_their_album(lib):
    # what an older version did: songs YouTube knows, filed under Playlists/
    add_file("Playlists/Road trip/001 - Tommy the Cat.m4a", "tommycat001", title="Tommy the Cat", track="Tommy the Cat",
             artist="Primus", album="Sailing the Seas of Cheese", release_year=1991, playlist_id=PL, playlist_index=1)
    meta._write_meta({"id": "tommycat001"}, {"track_number": 3})
    maintenance.tidy_music()
    assert files(lib) == ["Music/Primus/Sailing the Seas of Cheese (1991)/03 - Tommy the Cat.m4a"]


def test_tidy_merges_artist_and_album_spellings(lib):
    add_file("Music/MELVINS/Houdini/01 - Hooch.m4a", "hooch000001", title="Hooch", track="Hooch",
             artist="MELVINS", release_year=1993)
    add_file("Music/Melvins/Houdini (1993)/02 - Night Goat.m4a", "nightgoat01", title="Night Goat", track="Night Goat",
             artist="Melvins", release_year=1993)
    add_file("Music/Melvins/Houdini (1993)/03 - Lizzy.m4a", "lizzy000001", title="Lizzy", track="Lizzy",
             artist="Melvins", release_year=1993)
    maintenance.tidy_music()
    assert files(lib) == ["Music/Melvins/Houdini (1993)/01 - Hooch.m4a",
                          "Music/Melvins/Houdini (1993)/02 - Night Goat.m4a",
                          "Music/Melvins/Houdini (1993)/03 - Lizzy.m4a"]
    assert meta._index()["hooch000001:audio"] == "Music/Melvins/Houdini (1993)/01 - Hooch.m4a"


# ---------------------------------------------------------------- the library sees it

def test_library_lists_the_moved_collection(client, lib):
    add_file("Music/Uni/Singles/Lecture 1.m4a", "lecture0001", title="Lecture 1", uploader="Uni")
    reindex()
    keys = {c["key"] for c in json.loads(client.get("/api/library").data)["collections"]}
    assert "Music/Uni/Singles" in keys
    move(client, "Videos", key="Music/Uni/Singles")
    reindex()
    keys = {c["key"] for c in json.loads(client.get("/api/library").data)["collections"]}
    assert "Videos/Uni" in keys and "Music/Uni/Singles" not in keys
