export type Format = "video" | "audio";
export type JobStatus = "queued" | "downloading" | "complete" | "error" | "cancelled";

export interface Job {
  id: string;
  url: string;
  format: Format;
  playlist: boolean;
  status: JobStatus;
  title: string | null;
  error: string | null;
  percent: number | null;
  speed: number | null;
  eta: number | null;
  downloaded: number;
  total: number | null;
  current_file: string | null;
  playlist_index: number | null;
  playlist_count: number | null;
  entry_count: number | null;
  files: string[];
  created_at: string;
  finished_at: string | null;
}

export type Section = "Music" | "Videos" | "Playlists" | "Other";

export interface LibraryFile {
  /** path inside the library, e.g. "Music/Artist/Album (2025)/01 - Track.m4a" */
  name: string;
  title: string;
  kind: "video" | "audio";
  size: number;
  mtime: number;
  duration: number | null;
  height: number | null;
  section: Section;
  /** key of the collection (folder) it lives in, "" for loose files */
  collection: string;
  id: string | null;
  artist: string | null;
  album: string | null;
  uploader: string | null;
  upload_date: string | null;
  year: string | null;
  url: string | null;
  /** track / playlist position (from the "NN - " filename prefix) */
  index: number | null;
  /** album cover chosen for this track's album (cache key for its thumbnail) */
  art?: string | null;
  /** resume position (s) and finished flag, shared across devices */
  pos?: number;
  watched?: boolean;
  chapters?: Chapter[] | null;
  /** song lyrics found: line-synced, or plain text */
  lyrics?: "synced" | "plain" | null;
  /** English captions available (CC, transcript, search) */
  subs?: boolean;
}

export interface Collection {
  key: string;
  section: Section;
  kind: "album" | "singles" | "channel" | "playlist" | "folder";
  title: string;
  artist: string | null;
  year: string | null;
  url: string | null;
  count: number;
  size: number;
  duration: number;
  updated: number;
  covers: string[];
  /** cache token of a custom cover (a chosen item → covers[0] is that item) */
  art?: string | null;
  /** an uploaded picture, when there is one */
  cover_url?: string | null;
}

export interface Chapter { start: number; title: string }
export interface Segment { start: number; end: number; text: string }
export interface SearchResult { name: string; count: number; hits: { t: number; text: string }[] }

/** One of your own playlists: an ordered list of library items (no copies). */
export interface Playlist {
  id: string;
  title: string;
  description: string;
  /** library names, in playlist order */
  items: string[];
  created: string;
  updated: string;
  /** item whose thumbnail is the cover, or a version of an uploaded picture; neither → mosaic */
  cover_item: string | null;
  cover_upload: string | null;
}

export type PlaylistPatch = Partial<{ title: string; description: string; items: string[]; cover_item: string | null }>;

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, init);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || r.statusText || "Request failed");
  return body as T;
}

const post = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  jobs: () => req<{ jobs: Job[] }>("/api/jobs").then((r) => r.jobs),
  files: () => req<{ dir: string; files: LibraryFile[]; collections: Collection[]; playlists: Playlist[] }>("/api/files"),
  createPlaylist: (title: string, items: string[] = [], description = "") =>
    req<{ playlist: Playlist }>("/api/playlists", post({ title, items, description })).then((r) => r.playlist),
  updatePlaylist: (id: string, patch: PlaylistPatch) =>
    req<{ playlist: Playlist }>(`/api/playlists/${id}`, { ...post(patch), method: "PATCH" }).then((r) => r.playlist),
  deletePlaylist: (id: string) => req(`/api/playlists/${id}`, { method: "DELETE" }),
  progress: (name: string, body: { pos?: number; watched?: boolean }) => req("/api/progress", post({ name, ...body })),
  /** fire-and-forget that survives closing the tab */
  progressBeacon: (name: string, pos: number) =>
    navigator.sendBeacon?.("/api/progress", JSON.stringify({ name, pos })) || void api.progress(name, { pos }).catch(() => {}),
  lyrics: (name: string) => req<{ synced: Segment[] | null; plain: string | null }>(`/api/lyrics/${encPath(name)}`),
  findLyrics: (name: string) => req<{ found: "synced" | "plain" | null }>(`/api/lyrics/${encPath(name)}`, { method: "POST" }).then((r) => r.found),
  transcript: (name: string) => req<{ segments: Segment[] }>(`/api/transcript/${encPath(name)}`).then((r) => r.segments),
  search: (q: string) => req<{ results: SearchResult[] }>(`/api/search?q=${encodeURIComponent(q)}`).then((r) => r.results),
  uploadPlaylistCover: (id: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return req<{ playlist: Playlist }>(`/api/playlists/${id}/cover`, { method: "POST", body }).then((r) => r.playlist);
  },
  download: (url: string, format: Format, playlist: boolean) =>
    req<{ job: Job }>("/api/download", post({ url, format, playlist })).then((r) => r.job),
  cancel: (id: string) => req(`/api/jobs/${id}/cancel`, { method: "POST" }),
  remove: (name: string) => req("/api/files/delete", post({ name })),
  setCover: (key: string, name: string) => req("/api/collections/cover", post({ key, name })),
  deleteCollection: (key: string) => req<{ deleted: number }>("/api/collections/delete", post({ key })),
  uploadCollectionCover: (key: string, file: File) => {
    const body = new FormData();
    body.append("key", key);
    body.append("file", file);
    return req("/api/collections/cover/upload", { method: "POST", body });
  },
  editCollection: (key: string, fields: { title?: string; artist?: string; year?: string }) =>
    req<{ key: string }>("/api/collections/edit", post({ key, ...fields })).then((r) => r.key),
};

const encPath = (name: string) => name.split("/").map(encodeURIComponent).join("/");
export const mediaUrl = (name: string) => `/media/${encPath(name)}`;
/** `v` busts the browser cache when an album's chosen cover changes; `own` skips it. */
export const thumbUrl = (name: string, v?: string | null) => `/thumb/${encPath(name)}${v ? `?v=${encodeURIComponent(v)}` : ""}`;
export const ownThumbUrl = (name: string) => `/thumb/${encPath(name)}?own=1`;
export const isActive = (j: Job) => j.status === "queued" || j.status === "downloading";

/** Track/playlist order for albums and playlists, newest first otherwise. */
export function collectionOrder(c: Collection | undefined, files: LibraryFile[]): LibraryFile[] {
  const out = [...files];
  if (c && (c.kind === "album" || c.kind === "playlist")) {
    return out.sort((a, b) => (a.index ?? 1e9) - (b.index ?? 1e9) || a.title.localeCompare(b.title));
  }
  return out.sort((a, b) => b.mtime - a.mtime);
}

export const filesIn = (key: string, files: LibraryFile[]) => files.filter((f) => f.collection === key);
export const isMusic = (f: LibraryFile) => f.section === "Music";

/** What plays after `file`: rest of its collection (in order), then the rest of
 *  the same section (music keeps playing music), newest first. */
export function queueFor(file: LibraryFile, files: LibraryFile[], collections: Collection[]): LibraryFile[] {
  const coll = collections.find((c) => c.key === file.collection);
  const inColl = file.collection ? collectionOrder(coll, filesIn(file.collection, files)) : [];
  const i = inColl.findIndex((f) => f.name === file.name);
  const after = i >= 0 ? inColl.slice(i + 1) : [];
  const music = isMusic(file);
  const rest = files
    .filter((f) => f.collection !== file.collection && f.name !== file.name && isMusic(f) === music)
    .sort((a, b) => b.mtime - a.mtime);
  return [...after, ...rest];
}

/** The item before `file` in its album / playlist (what "previous" goes to). */
export function prevInCollection(file: LibraryFile, files: LibraryFile[], collections: Collection[]): LibraryFile | null {
  const coll = collections.find((c) => c.key === file.collection);
  if (!coll || (coll.kind !== "album" && coll.kind !== "playlist")) return null;
  const order = collectionOrder(coll, filesIn(file.collection, files));
  const i = order.findIndex((f) => f.name === file.name);
  return i > 0 ? order[i - 1] : null;
}

export const playlistCoverUrl = (p: Playlist) => `/playlist-cover/${p.id}?v=${p.cover_upload}`;

/** A playlist's items that still exist, in order. */
export function playlistFiles(p: Playlist, files: LibraryFile[]): LibraryFile[] {
  const by = new Map(files.map((f) => [f.name, f]));
  return p.items.map((n) => by.get(n)).filter((f): f is LibraryFile => !!f);
}

/** Next / previous inside a playlist (no wrap-around). */
export function playlistNeighbours(p: Playlist, file: LibraryFile, files: LibraryFile[]) {
  const items = playlistFiles(p, files);
  const i = items.findIndex((f) => f.name === file.name);
  return { after: i >= 0 ? items.slice(i + 1) : items, before: i > 0 ? items[i - 1] : null, position: i };
}

/** What plays after `file`: inside a playlist, the rest of it; otherwise its album/collection, then the section. */
export function upNextFor(file: LibraryFile, files: LibraryFile[], collections: Collection[], playlist?: Playlist | null) {
  if (playlist && playlist.items.includes(file.name)) return playlistNeighbours(playlist, file, files).after;
  return queueFor(file, files, collections);
}

/** A collection's main picture: uploaded, else its (chosen or first) item's thumbnail. */
export const collectionCoverUrl = (c: Collection) =>
  c.cover_url ?? (c.covers[0] ? thumbUrl(c.covers[0], c.art) : null);

/** Long-form items remember where you stopped (not 3-minute songs). */
export const resumable = (f: LibraryFile) => f.kind === "video" || (f.duration ?? 0) > 600;
