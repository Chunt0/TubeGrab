# TubeGrab API (the parts for music and playlists)

Base: `$TUBEGRAB_URL`, default `http://192.168.254.10:8765`. JSON in and out. A
library *name* is a file's path inside the library, e.g.
`Music/Primus/Frizzle Fry (1990)/03 - Too Many Puppies.m4a`.

## Reading

- `GET /api/library`: `collections[]`, every album / singles folder / channel /
  playlist folder: `key` (folder path), `section` ("Music" for music), `kind`
  (album, singles, channel, playlist, folder), `title`, `artist`, `year`,
  `count`, `duration` (s), `genres` (music: from MusicBrainz, may be `[]`).
- `GET /api/files`: files, paged. Parameters:
  - `section=Music` (music only) or `section=videos`
  - `q=<words>`: all words in title / artist / album / folder (accents, case
    and punctuation ignored)
  - `dir=<collection key>`: one album or folder
  - `fav=1`: hearted songs only
  - `sort=artist|title|new|old|plays|played|long|size`
  - `limit` (≤ 2000) and `offset`

  Each file: `name`, `title`, `artist`, `album`, `year`, `duration`, `id`
  (YouTube id or null), `kind` (audio / video), `collection` (its folder key:
  look up genres there), `index` (track number), `lyrics`, `plays`,
  `last_played` (ISO time or null). Answer: `{total, files}`.
- `GET /api/favorites`: `{artists: [name], albums: [collection key], songs: [ref]}`.
  A song's ref is `"<id>:<kind>"`, or `"path:<name>"` for a file without an id.
- `GET /api/dislikes`: the same shape: what the user marked as disliked. The app
  only records these (nothing is hidden); an item is never both hearted and disliked.
- `GET /api/playlists`: `playlists[]`: `id`, `title`, `description`, `items`
  (names, in order), `count`, `duration`.
- `GET /api/playlists/<id>`: the same plus `files` (full file entries).

## Writing

- `POST /api/playlists` `{title, description?, items: [name]}` → `{playlist}`.
  Titles are cut at 120 characters, descriptions at 1000; unknown names are refused (400).
- `PATCH /api/playlists/<id>` with any of `{title, description, items}`
  (`items`: the whole new list, in order).
- `POST /api/favorites` `{kind: "artist"|"album"|"song", id, on}`: id is the
  artist name, the album's collection key, or the song's name. Answer: the
  favorites. Hearting something takes away its dislike.
- `POST /api/dislikes`: the same for dislikes (disliking takes away the heart).
- `POST /api/files/delete` `{name}`: deletes one file for good.
- `POST /api/collections/delete` `{key}`: deletes an album or singles folder and
  every file in it (409 while downloads run). Deleting is for good: only when the
  user asked, after they've seen the list.

A song dislike is kept after its file is deleted (it then matches no file); an
album's goes with its folder.

Playlists reference files (by id, so they survive renames and re-filing); they
never copy or change the music. The web page for a playlist is `/p/<id>`.
