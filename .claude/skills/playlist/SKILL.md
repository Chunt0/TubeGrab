---
name: playlist
description: Make or change playlists from the user's TubeGrab music library (their self-hosted YouTube/SoundCloud library on the NAS). Use when they ask for a playlist, a mix, "songs for …", "something like …", a playlist from their favorites or most played, or to add to / reorder / rename one of their playlists. Also for questions about what's in their music library (genres, artists, what they play most, what they dislike), and for clearing out songs or albums they disliked.
---

# Playlists from the TubeGrab library

The library lives on the NAS; `tg.py` (next to this file) reads it and makes
playlists through TubeGrab's API. Playlists only point at files: making one
costs nothing and never touches the music.

```sh
TG="python3 ~/.claude/skills/playlist/tg.py"   # (or this skill's folder)
$TG stats                 # start here: size, genres, decades, most played, favorites, playlists
$TG songs -h              # every filter
```

## Steps

1. **Look before choosing.** Run `$TG stats` once. Then narrow with filters
   instead of listing everything: `$TG songs --genre grunge --years 1990-1999`,
   `$TG songs --fav-any --sort plays`, `$TG albums --genre jazz`. Output is
   tab-separated; the first column of `songs` is the song's id.
2. **Choose the songs yourself.** Genres come from MusicBrainz per album (or the
   artist's, when the album has none) and can be missing or broad: use what you
   know about the artists and songs too, and check `albums` when a genre seems
   off. Use only ids that `songs` printed: the playlist can only hold what's in
   the library.
3. **Create it**, ids as arguments or piped one per line:
   ```sh
   $TG create "Rainy Sunday" -d "Slow, warm, mostly acoustic" ID1 ID2 ID3 …
   $TG songs --genre "trip hop" --sort random --limit 25 | $TG create "Trip hop, shuffled" -
   ```
   It prints the playlist's id and link. Give the user the link.
4. **Changing one:** `$TG playlists`, `$TG show ID`, then `add`, `replace` (the
   new full order) or `rename`. There is no delete here: if the user wants one
   gone, say they can delete it from its ⋯ menu.

## Making a good playlist

- **Length:** what they ask for; otherwise about an hour (15–20 songs). The
  `songs` footer gives the total minutes.
- **Variety:** no more than 2–3 songs per artist and 1–2 per album unless they
  ask for one artist, and don't put the same artist back to back.
- **Order matters:** open strong, keep neighbours compatible in tempo and mood,
  build and ease off; don't just sort by artist.
- **Their taste:** favorites (`♥` = hearted song; `·` = from a hearted album or
  artist) and plays (`--sort plays`, `--min-plays`) say what they like.
  "Rediscover" requests: `--not-since 90` or `--unplayed`.
- **Leave out** long mixes and full-album uploads unless asked
  (`--max-minutes 12`), and duplicates (the same song on two albums).
- **Title and description:** short and plain; the description says what it is
  ("90s alternative, loud to quiet"), not how it was made.
- Don't change or rename the user's existing playlists unless asked.
- **Dislikes:** `songs` already leaves out disliked songs and the songs of
  disliked albums and artists. Don't add them back (`--with-disliked`) unless
  asked, and steer away from what's close to them.

## Dislikes and deleting

The user marks songs, albums and artists they don't like in the app (a broken heart); the
app only records it. `$TG dislikes` lists them (with album keys and song ids).

When they ask you to clean up or delete what they disliked:

1. Show what would go: `$TG delete ID… --album KEY… --artist NAME` without
   `--yes` lists the files and the count, and deletes nothing.
2. Deleting is for good (no trash). Go ahead only when they've seen that list
   and said yes, or when they named exactly what to delete; then run the same
   command with `--yes`. Never delete things they didn't dislike or name.
3. Downloads must be finished for albums (`Wait for downloads to finish first`):
   try again later.

`$TG dislike|undislike song|album|artist X…` changes the marks, only when they ask.

## When something's off

- `can't reach`: the server is at `$TUBEGRAB_URL` (default: the NAS on the home
  network, `http://192.168.254.10:8765`). Outside the home network the public
  address is behind a login a script can't pass.
- "genres known for 0 of N": the NAS fetches them in the background after a
  start or a download (one request a second); try again later.
- The API itself (for anything `tg.py` doesn't do): see `reference.md`.
