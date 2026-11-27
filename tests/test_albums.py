"""Recognising a playlist of fan uploads as one album (MusicBrainz lookups are faked)."""
import json

import pytest

import app as tg
from conftest import Offline

FRIZZLE_TRACKS = ["To Defy the Laws of Tradition", "Ground Hog's Day", "Too Many Puppies", "Mr. Knowitall",
                  "Frizzle Fry", "John the Fisherman", "You Can't Kill Michael Malloy Twice", "Pudding Time",
                  "Sathington Willoughby", "Spegetti Western", "Harold of the Rocks", "To Defy",
                  "The Toys Go Winding Down"]

# what fans call them on YouTube, in playlist order
FAN_TITLES = ["Primus- To Defy The Laws Of Tradition", "Primus - Groundhogs Day (HQ)", "PRIMUS: Too Many Puppies",
              "Primus- Mr. Knowitall- Frizzle Fry", "Primus - Frizzle Fry", "Primus - John The Fisherman",
              "Primus - You Can't Kill Michael Malloy Twice", "Primus - Pudding Time",
              "Primus - Sathington Willoughby", "PRIMUS: Spaghetti Western", "Primus - Harold Of The Rocks",
              "Primus - To Defy", "Primus - The Toys Go Winding Down"]


def entries(titles):
    return [{"id": f"v{i:010d}", "title": t} for i, t in enumerate(titles, 1)]


class FakeMB:
    """Answers the two MusicBrainz calls _mb_release makes; counts them."""

    def __init__(self, tracks=FRIZZLE_TRACKS, title="Frizzle Fry", artist="Primus", date="1990-02-01", found=True):
        self.calls = []
        self.release = {"id": "rel-1", "title": title, "score": 100, "track-count": len(tracks), "status": "Official",
                        "release-group": {"primary-type": "Album"}}
        self.full = {"id": "rel-1", "title": title, "date": "2002",
                     "artist-credit": [{"name": artist, "joinphrase": ""}],
                     "release-group": {"id": "rg-1", "first-release-date": date},
                     "media": [{"tracks": [{"title": t} for t in tracks]}]}
        self.found = found

    def __call__(self, url, *, mb=False):
        self.calls.append(url)
        if "/release/?" in url:
            return {"releases": [self.release] if self.found else []}
        return self.full


# ---------------------------------------------------------------- guessing from titles

def test_guess_fan_playlist():
    g = tg._guess_album("Primus - Frizzle Fry (Full Album)", entries(FAN_TITLES))
    assert g == {"artist": "Primus", "album": "Frizzle Fry", "year": None, "said": True}


def test_guess_takes_a_bracketed_year():
    g = tg._guess_album("Primus - Frizzle Fry (1990) [Full Album]", entries(FAN_TITLES))
    assert (g["album"], g["year"]) == ("Frizzle Fry", "1990")


def test_guess_does_not_take_an_album_named_like_a_year():
    titles = ["Taylor Swift - Style (Taylor's Version)", "Taylor Swift - Blank Space (Taylor's Version)",
              "Taylor Swift - Shake It Off (Taylor's Version)"]
    g = tg._guess_album("Taylor Swift - 1989 (Taylor's Version)", entries(titles))
    assert g["artist"] == "Taylor Swift"
    assert g["album"].startswith("1989")
    assert g["year"] is None


def test_guess_without_artist_in_entry_titles():
    g = tg._guess_album("Nirvana Nevermind Full Album", entries(["Smells Like Teen Spirit", "In Bloom", "Come as You Are"]))
    assert g == {"artist": None, "album": "Nirvana Nevermind", "year": None, "said": True}
    readings = [(x["artist"], x["album"]) for x in tg._guesses(g)]
    assert ("Nirvana", "Nevermind") in readings


def test_guesses_split_artist_and_album():
    g = {"artist": None, "album": "Nirvana - Nevermind", "year": None, "said": True}
    assert [(x["artist"], x["album"]) for x in tg._guesses(g)] == [("Nirvana", "Nevermind"), ("Nevermind", "Nirvana")]


def test_mixed_playlist_is_not_an_album():
    titles = ["Primus - Tommy the Cat", "Melvins - Honey Bucket", "Tool - Sober", "Ween - Ocean Man"]
    assert tg._guess_album("Road trip", entries(titles)) is None


def test_too_short_to_be_an_album():
    assert tg._guess_album("Primus - Frizzle Fry (Full Album)", entries(FAN_TITLES[:2])) is None


@pytest.mark.parametrize("title, want", [
    ("Primus- Mr. Knowitall- Frizzle Fry", "Mr. Knowitall"),
    ("03. Too Many Puppies.wmv", "Too Many Puppies"),
    ("Primus - Pudding Time (Official Audio)", "Pudding Time"),
    ("Track 7 - Groundhogs Day", "Groundhogs Day"),
])
def test_local_title(title, want):
    assert tg._local_title(title, "Primus", "Frizzle Fry") == want


def test_norm_is_loose():
    assert tg._norm("Hello Skinny / Constantinople") == tg._norm("hello skinny-constantinople")
    assert tg._norm("Beyoncé & Jay-Z") == "beyonce and jay z"
    assert tg._norm("Song (Remastered 2011)") == "song"


# ---------------------------------------------------------------- matching tracks

def test_match_tracks_pairs_each_track_once():
    es = entries(["Primus - Groundhogs Day", "Primus - Too Many Puppies", "Primus - Some Interview"])
    pairs = tg._match_tracks(es, ["Too Many Puppies", "Groundhog's Day"], {"artist": "Primus", "album": "Frizzle Fry"})
    assert pairs == {0: 1, 1: 0}


def test_match_tracks_whole_word_substring():
    es = entries(["Primus Frizzle Fry Mr Knowitall live 1990"])
    pairs = tg._match_tracks(es, ["Mr. Knowitall"], {"artist": "Primus", "album": "Frizzle Fry"})
    assert pairs == {0: 0}


# ---------------------------------------------------------------- identifying (cached)

def test_identify_fan_playlist(monkeypatch):
    mb = FakeMB()
    monkeypatch.setattr(tg, "_http_json", mb)
    es = entries(FAN_TITLES)
    a = tg._identify_album("PL1", "Primus - Frizzle Fry (Full Album)", es)
    assert (a["artist"], a["album"], a["year"], a["mbid"]) == ("Primus", "Frizzle Fry", "1990", "rel-1")
    assert a["tracks"]["v0000000001"] == {"no": 1, "title": "To Defy the Laws of Tradition"}
    assert a["tracks"]["v0000000010"] == {"no": 10, "title": "Spegetti Western"}   # MusicBrainz's spelling
    assert sorted(t["no"] for t in a["tracks"].values()) == list(range(1, 14))

    cached = json.loads(tg.ALBUMS_FILE.read_text())
    assert cached["PL1"]["album"] == "Frizzle Fry"
    n = len(mb.calls)
    assert tg._identify_album("PL1", "Primus - Frizzle Fry (Full Album)", es) == a
    assert len(mb.calls) == n   # answered from the cache


def test_identify_keeps_extra_entries_without_a_number(monkeypatch):
    monkeypatch.setattr(tg, "_http_json", FakeMB())
    es = entries(FAN_TITLES + ["Primus - Les Claypool Interview 1990"])
    a = tg._identify_album("PL1", "Primus - Frizzle Fry (Full Album)", es)
    assert a["tracks"]["v0000000014"] == {"no": None, "title": "Les Claypool Interview 1990"}


def test_identify_rejects_a_poor_match(monkeypatch):
    monkeypatch.setattr(tg, "_http_json", FakeMB(tracks=["Completely", "Different", "Songs", "Here"]))
    titles = ["Primus - Tommy the Cat", "Primus - Jerry Was a Race Car Driver", "Primus - My Name Is Mud"]
    assert tg._identify_album("PL2", "Primus - Best of", entries(titles)) is None
    assert json.loads(tg.ALBUMS_FILE.read_text())["PL2"] == {"none": True}


def test_identify_unknown_album_but_playlist_says_full_album(monkeypatch):
    monkeypatch.setattr(tg, "_http_json", FakeMB(found=False))
    a = tg._identify_album("PL3", "Primus - Frizzle Fry (Full Album)", entries(FAN_TITLES[:4]))
    assert (a["artist"], a["album"]) == ("Primus", "Frizzle Fry")
    assert "mbid" not in a
    assert a["tracks"]["v0000000004"] == {"no": 4, "title": "Mr. Knowitall"}   # playlist order


def test_identify_offline_decides_from_titles_and_asks_again_later():
    a = tg._identify_album("PL4", "Primus - Frizzle Fry (Full Album)", entries(FAN_TITLES[:4]))
    assert a["album"] == "Frizzle Fry"
    assert a["tracks"]["v0000000001"]["no"] == 1
    assert "PL4" in json.loads(tg.ALBUMS_FILE.read_text())


def test_identify_offline_without_album_wording_is_not_cached():
    titles = ["Primus - Tommy the Cat", "Primus - Jerry Was a Race Car Driver", "Primus - My Name Is Mud"]
    assert tg._identify_album("PL5", "Primus - Sailing the Seas of Cheese", entries(titles)) is None
    assert "PL5" not in tg._read_json(tg.ALBUMS_FILE, {})


def test_identify_negative_answer_is_cached(monkeypatch):
    mb = FakeMB(found=False)
    monkeypatch.setattr(tg, "_http_json", mb)
    titles = ["Primus - Tommy the Cat", "Primus - Jerry Was a Race Car Driver", "Primus - My Name Is Mud"]
    assert tg._identify_album("PL6", "Primus - Sailing the Seas of Cheese", entries(titles)) is None
    n = len(mb.calls)
    assert tg._identify_album("PL6", "Primus - Sailing the Seas of Cheese", entries(titles)) is None
    assert len(mb.calls) == n


def test_identify_asks_again_when_the_playlist_grew(monkeypatch):
    mb = FakeMB()
    monkeypatch.setattr(tg, "_http_json", mb)
    tg._identify_album("PL7", "Primus - Frizzle Fry (Full Album)", entries(FAN_TITLES[:10]))
    n = len(mb.calls)
    tg._identify_album("PL7", "Primus - Frizzle Fry (Full Album)", entries(FAN_TITLES))
    assert len(mb.calls) > n


def test_offline_fake_really_is_offline():
    with pytest.raises(Offline):
        tg._http_json("https://musicbrainz.org/ws/2/release/?query=x")
