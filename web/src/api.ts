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
  /** album cover chosen for this track's album */
  art?: string | null;
  /** thumbnail version: thumbnail URLs carry it, so browsers can keep them forever */
  tv: string;
  /** average colour of the thumbnail, shown while it loads */
  color?: string | null;
  /** only from /api/watch */
  chapters?: Chapter[] | null;
  /** song lyrics found: line-synced, or plain text */
  lyrics?: "synced" | "plain" | null;
  /** English captions available (CC, transcript, search) */
  subs?: boolean;
}

/** Enough of a file to draw its thumbnail. */
export interface Tile { name: string; tv: string; color?: string | null; kind?: "video" | "audio" }

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
  /** first item in play order */
  first: string;
  /** up to three items for the cover (a chosen cover item comes first) */
  covers: Tile[];
  /** token of a custom cover (a chosen item, or an uploaded picture) */
  art?: string | null;
  /** an uploaded picture, when there is one */
  cover_url?: string | null;
}

export interface Stat { count: number; size: number; duration: number; audio: number }

/** Everything small enough to keep on the device: all collections + totals. */
export interface Library {
  dir: string;
  /** false while the server is still building its index for the first time */
  ready: boolean;
  collections: Collection[];
  /** watched videos per collection */
  watched: Record<string, number>;
  stats: { music: Stat; videos: Stat };
}

export interface Page { total: number; files: LibraryFile[] }

export interface WatchData {
  file: LibraryFile;
  collection: Collection | null;
  playlist: { id: string; title: string; position: number; total: number } | null;
  prev: LibraryFile | null;
  /** what plays next, in order */
  next: LibraryFile[];
}

export interface Chapter { start: number; title: string }
export interface Segment { start: number; end: number; text: string }
export interface SearchResult { name: string; count: number; hits: { t: number; text: string }[]; file: LibraryFile }

/** One of your own playlists: an ordered list of library items (no copies). */
export interface Playlist {
  id: string;
  title: string;
  description: string;
  /** library names, in playlist order */
  items: string[];
  count: number;
  duration: number;
  created: string;
  updated: string;
  /** item whose thumbnail is the cover, or a version of an uploaded picture; neither → mosaic */
  cover_item: string | null;
  cover_tile: Tile | null;
  cover_upload: string | null;
  mosaic: Tile[];
  /** only from /api/playlists/<id> */
  files?: LibraryFile[];
}

export type PlaylistPatch = Partial<{ title: string; description: string; items: string[]; cover_item: string | null }>;

export class HttpError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, init);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new HttpError(body.error || r.statusText || "Request failed", r.status);
  return body as T;
}

const post = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const encPath = (name: string) => name.split("/").map(encodeURIComponent).join("/");
const qs = (params: Record<string, string | number | null | undefined>) => {
  const s = Object.entries(params).filter(([, v]) => v != null && v !== "")
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&");
  return s ? `?${s}` : "";
};

/** Query URLs (fetched through lib/cache, so they're cached on the device). */
export const urls = {
  library: "/api/library",
  playlists: "/api/playlists",
  progress: "/api/progress",
  playlist: (id: string) => `/api/playlists/${encodeURIComponent(id)}`,
  watch: (name: string, list?: string | null) => `/api/watch/${encPath(name)}${qs({ list })}`,
  files: (p: { dir?: string; section?: "Music" | "videos"; kind?: string; q?: string; sort?: string }) => `/api/files${qs(p)}`,
};

export const api = {
  jobs: () => req<{ jobs: Job[] }>("/api/jobs").then((r) => r.jobs),
  names: (dir: string) => req<{ names: string[] }>(`/api/files${qs({ dir, only: "names" })}`).then((r) => r.names),
  random: (scope: { dir?: string; artist?: string; list?: string }) =>
    req<{ name: string | null }>(`/api/random${qs(scope)}`).then((r) => r.name),
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
  search: (q: string) => req<{ results: SearchResult[] }>(`/api/search${qs({ q })}`).then((r) => r.results),
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
  /** a file (`name`) or a whole collection (`key`) → Music, or the video side */
  move: (target: { name: string } | { key: string }, to: "Music" | "Videos") =>
    req<{ name?: string; key?: string | null }>("/api/move", post({ ...target, to })),
  editCollection: (key: string, fields: { title?: string; artist?: string; year?: string }) =>
    req<{ key: string }>("/api/collections/edit", post({ key, ...fields })).then((r) => r.key),
};

export const mediaUrl = (name: string) => `/media/${encPath(name)}`;
/** `v` (the thumbnail version) makes the URL permanent: browsers keep it without asking again. */
export const thumbUrl = (name: string, v?: string | null, full = false) => `/thumb/${encPath(name)}${qs({ v, full: full ? 1 : null })}`;
export const ownThumbUrl = (name: string, v?: string | null) => `/thumb/${encPath(name)}${qs({ own: 1, v })}`;
export const isActive = (j: Job) => j.status === "queued" || j.status === "downloading";

export const playlistCoverUrl = (p: Playlist) => `/playlist-cover/${p.id}?v=${p.cover_upload}`;

/** A collection's main picture: uploaded, else its (chosen or first) item's thumbnail. */
export const collectionCoverUrl = (c: Collection) =>
  c.cover_url ?? (c.covers[0] ? thumbUrl(c.covers[0].name, c.covers[0].tv) : null);

/** Long-form items remember where you stopped (not 3-minute songs). */
export const resumable = (f: Pick<LibraryFile, "kind" | "duration">) => f.kind === "video" || (f.duration ?? 0) > 600;
