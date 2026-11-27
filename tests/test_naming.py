"""Where a download is filed (_plan) and the name helpers it uses."""
import pytest

from tubegrab import naming

PL = "PLxxxxxxxxxxxxxxxx"


def plan(info, *, audio=True, playlist=False, album=None):
    return naming._plan({"id": "vid00000001", **info}, audio=audio, playlist_mode=playlist, album=album)


# ---------------------------------------------------------------- helpers

@pytest.mark.parametrize("raw, want", [
    ("AC/DC: Back in Black", "AC-DC - Back in Black"),
    ('What? "Really" <yes>|*', "What Really yes"),
    ("...hidden", "hidden"),
    ("Etc...", "Etc"),
    ("  lots   of\tspace ", "lots of space"),
    ("", "Unknown"),
    (None, "Unknown"),
    ("back\\slash", "back-slash"),
    ("Primus | Live", "Primus Live"),
    ("line\nbreak", "line break"),
])
def test_clean(raw, want):
    assert naming._clean(raw) == want


def test_clean_limits_length():
    assert len(naming._clean("x" * 300)) == 120


def test_clean_channel_drops_topic():
    assert naming._clean_channel("Coldsteel - Topic") == "Coldsteel"
    assert naming._clean_channel("Topic Talks") == "Topic Talks"
    assert naming._clean_channel(None) is None


@pytest.mark.parametrize("info, want", [
    ({"release_year": 1991}, "1991"),
    ({"release_date": "19910514"}, "1991"),
    ({"release_date": "unknown"}, None),
    ({}, None),
])
def test_year(info, want):
    assert naming._year(info) == want


@pytest.mark.parametrize("info, want", [
    ({"album_artist": "Primus", "artist": "Les Claypool"}, "Primus"),
    ({"artists": ["Taylor Swift", "Post Malone"]}, "Taylor Swift"),
    ({"artist": "Taylor Swift, Post Malone"}, "Taylor Swift"),
    ({"creators": ["Melvins"]}, "Melvins"),
    ({"artists": []}, None),
    ({}, None),
])
def test_artist(info, want):
    assert naming._artist(info) == want


@pytest.mark.parametrize("title, artist, want", [
    ("Taylor Swift - Fortnight (feat. Post Malone) (Official Music Video)", "Taylor Swift", "Fortnight (feat. Post Malone)"),
    ("Song [Official Lyric Video]", None, "Song"),
    ("Song (Visualizer)", None, "Song"),
    ("Song (Official Audio HD)", None, "Song"),
    ("Song (Live at Wembley)", None, "Song (Live at Wembley)"),
    ("Song [Remix]", None, "Song [Remix]"),
    ("(Official Video)", None, "(Official Video)"),   # nothing left: keep the original
])
def test_track_title(title, artist, want):
    assert naming._track_title(title, artist) == want


def test_artist_dir_reuses_existing_spelling(lib):
    (lib / "Music" / "MELVINS").mkdir(parents=True)
    assert naming._artist_dir("Melvins") == "MELVINS"
    assert naming._artist_dir("Primus") == "Primus"


# ---------------------------------------------------------------- music

def test_youtube_music_album_track():
    p = plan({"track": "Tommy the Cat", "title": "Tommy the Cat", "artist": "Primus",
              "album": "Sailing the Seas of Cheese", "release_year": 1991,
              "playlist_id": "OLAK5uy_abc", "playlist_title": "Album - Sailing the Seas of Cheese",
              "playlist_index": 3, "playlist_webpage_url": "https://music.youtube.com/playlist?list=OLAK5uy_abc"},
             playlist=True)
    assert p["rel"] == "Music/Primus/Sailing the Seas of Cheese (1991)/03 - Tommy the Cat"
    assert p["section"] == "Music"
    assert p["mixed_playlist"] is False
    assert p["tags"]["track_number"] == 3
    assert p["collection_url"].endswith("OLAK5uy_abc")


def test_single_song_with_album_info():
    p = plan({"track": "Here Come the Bastards", "artist": "Primus", "album": "Sailing the Seas of Cheese",
              "release_year": 1991, "track_number": 13})
    assert p["rel"] == "Music/Primus/Sailing the Seas of Cheese (1991)/13 - Here Come the Bastards"


def test_song_without_album_goes_to_singles():
    p = plan({"track": "Fortnight", "artist": "Taylor Swift"})
    assert p["rel"] == "Music/Taylor Swift/Singles/Fortnight"
    assert p["tags"]["track_number"] is None


def test_music_video_title_is_cleaned():
    p = plan({"title": "Taylor Swift - Fortnight (feat. Post Malone) (Official Music Video)", "artist": "Taylor Swift"})
    assert p["rel"] == "Music/Taylor Swift/Singles/Fortnight (feat. Post Malone)"


def test_audio_without_music_info_reads_artist_from_title():
    p = plan({"title": "Primus - Jerry Was a Race Car Driver (Official Audio)", "uploader": "Some Fan"})
    assert p["rel"] == "Music/Primus/Singles/Jerry Was a Race Car Driver"
    assert p["section"] == "Music"


def test_audio_without_music_info_uses_the_channel():
    p = plan({"title": "A talk about bass", "uploader": "Coldsteel - Topic"})
    assert p["rel"] == "Music/Coldsteel/Singles/A talk about bass"


def test_audio_without_any_names():
    assert plan({"title": "Something"})["rel"] == "Music/Unknown/Singles/Something"


def test_long_prefix_is_not_an_artist():
    title = "A very long sentence that is clearly not an artist name - part two"
    assert plan({"title": title, "uploader": "Uni"})["rel"] == f"Music/Uni/Singles/{title}"


def test_mixed_playlist_song_files_into_its_album():
    p = plan({"track": "Fortnight", "artist": "Taylor Swift", "album": "The Tortured Poets Department",
              "release_year": 2024, "track_number": 2, "playlist_id": PL, "playlist_title": "Road trip",
              "playlist_index": 7}, playlist=True)
    assert p["rel"] == "Music/Taylor Swift/The Tortured Poets Department (2024)/02 - Fortnight"
    assert p["mixed_playlist"] is True


def test_mixed_playlist_unknown_song_goes_to_singles_and_the_playlist():
    p = plan({"title": "Primus - Tommy the Cat", "uploader": "Fan", "playlist_id": PL, "playlist_title": "Road trip",
              "playlist_index": 4}, playlist=True)
    assert p["rel"] == "Music/Primus/Singles/Tommy the Cat"
    assert p["mixed_playlist"] is True


def test_playlist_that_is_one_album():
    # "The Smashing Pumpkins: Gish (Deluxe Edition)": numbered by playlist position
    p = plan({"track": "Rhinoceros", "artist": "The Smashing Pumpkins", "album": "Gish", "track_number": 1,
              "playlist_id": PL, "playlist_title": "The Smashing Pumpkins: Gish (Deluxe Edition)", "playlist_index": 5,
              "playlist_webpage_url": "https://www.youtube.com/playlist?list=" + PL}, playlist=True)
    assert p["rel"] == "Music/The Smashing Pumpkins/Gish/05 - Rhinoceros"
    assert p["mixed_playlist"] is False
    assert p["collection_url"].endswith(PL)


def test_existing_album_folder_is_reused(lib):
    (lib / "Music" / "Primus" / "Frizzle Fry (1990)").mkdir(parents=True)
    p = plan({"track": "John the Fisherman", "artist": "PRIMUS", "album": "Frizzle Fry", "track_number": 3})
    assert p["rel"] == "Music/Primus/Frizzle Fry (1990)/03 - John the Fisherman"
    assert p["tags"]["release_year"] == "1990"
    assert p["tags"]["album_artist"] == "Primus"


# ---------------------------------------------------------------- identified albums (fan uploads)

FRIZZLE = {"artist": "Primus", "album": "Frizzle Fry", "year": "1990", "url": "https://www.youtube.com/playlist?list=" + PL,
           "guess_artist": "Primus", "guess_album": "Frizzle Fry",
           "tracks": {"vid00000001": {"no": 1, "title": "To Defy the Laws of Tradition"}}}


def test_identified_album_entry():
    p = plan({"title": "Primus- To Defy The Laws Of Tradition", "uploader": "Fan", "playlist_id": PL,
              "playlist_title": "Primus - Frizzle Fry (Full Album)", "playlist_index": 1}, playlist=True, album=FRIZZLE)
    assert p["rel"] == "Music/Primus/Frizzle Fry (1990)/01 - To Defy the Laws of Tradition"
    assert p["tags"]["album"] == "Frizzle Fry"
    assert p["mixed_playlist"] is False


def test_identified_album_entry_not_on_the_release():
    p = naming._plan({"id": "vid00000099", "title": "Primus - Bonus Jam - Frizzle Fry", "playlist_id": PL, "playlist_index": 15},
                 audio=True, playlist_mode=True, album=FRIZZLE)
    assert p["rel"] == "Music/Primus/Frizzle Fry (1990)/Bonus Jam"


def test_album_hint_ignored_when_youtube_knows_the_album():
    p = plan({"track": "Tommy the Cat", "artist": "Primus", "album": "Sailing the Seas of Cheese", "track_number": 3,
              "playlist_id": PL, "playlist_title": "Mix"}, playlist=True, album=FRIZZLE)
    assert p["rel"].startswith("Music/Primus/Sailing the Seas of Cheese/")


# ---------------------------------------------------------------- video

def test_single_video():
    p = plan({"title": "Lecture 1: Intro", "uploader": "MIT OpenCourseWare",
              "channel_url": "https://www.youtube.com/@mitocw"}, audio=False)
    assert p["rel"] == "Videos/MIT OpenCourseWare/Lecture 1 - Intro"
    assert p["section"] == "Videos"
    assert p["collection_url"] == "https://www.youtube.com/@mitocw"


def test_video_playlist():
    p = plan({"title": "Week 3", "uploader": "Uni", "playlist_id": PL, "playlist_title": "Course: Bass",
              "playlist_index": 3}, audio=False, playlist=True)
    assert p["rel"] == "Playlists/Course - Bass/003 - Week 3"
    assert p["section"] == "Playlists"


def test_video_in_playlist_downloaded_alone():
    # a playlist URL with "just this video": not playlist mode
    p = plan({"title": "Week 3", "uploader": "Uni", "playlist_id": PL, "playlist_index": 3}, audio=False)
    assert p["rel"] == "Videos/Uni/Week 3"
