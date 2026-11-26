import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api, isActive, urls, type Collection, type Format, type Job, type Library, type LibraryFile, type Playlist, type PlaylistPatch } from "./api";
import { fetchQuery, revalidateAll, setQueryData, setVersion, useQuery } from "./lib/cache";

export interface Progress { pos: number; watched: boolean }

export interface Toast { id: number; kind: "ok" | "bad" | "info"; title: string; body?: string }

interface Store {
  /** all collections + totals (null until the first answer, from the device or the server) */
  library: Library | null;
  jobs: Job[];
  activeCount: number;
  /** re-check everything on screen with the server */
  refresh: () => void;
  grab: (url: string, format: Format, playlist: boolean) => Promise<Job>;
  cancel: (id: string) => Promise<void>;
  remove: (file: LibraryFile) => Promise<void>;
  editCollection: (key: string, fields: { title?: string; artist?: string; year?: string }) => Promise<string>;
  setCover: (key: string, name: string) => Promise<void>;
  uploadCollectionCover: (key: string, file: File) => Promise<void>;
  deleteCollection: (c: Collection) => Promise<void>;
  /** to Music, or to the video side; resolves to the new name (file) or key (collection) */
  move: (target: { name: string } | { key: string }, to: "Music" | "Videos") => Promise<string | null>;
  /** resume position + watched, by library name */
  progress: Record<string, Progress>;
  noteProgress: (name: string, p: Partial<Progress>) => void;
  setWatched: (f: LibraryFile, watched: boolean) => Promise<void>;
  playlists: Playlist[];
  playlistsLoaded: boolean;
  createPlaylist: (title: string, items?: string[], description?: string) => Promise<Playlist>;
  updatePlaylist: (id: string, patch: PlaylistPatch) => Promise<Playlist>;
  deletePlaylist: (id: string) => Promise<void>;
  uploadPlaylistCover: (id: string, file: File) => Promise<Playlist>;
  /** "Add to playlist" picker for these library names */
  addTarget: { names: string[]; label: string } | null;
  openAddToPlaylist: (names: string[], label?: string) => void;
  closeAddToPlaylist: () => void;
  toasts: Toast[];
  toast: (t: Omit<Toast, "id">) => void;
  dismiss: (id: number) => void;
  grabOpen: boolean;
  grabPrefill: string;
  openGrab: (prefill?: string) => void;
  closeGrab: () => void;
}

const Ctx = createContext<Store | null>(null);

export function StoreProvider({ children }: { children: ReactNode }) {
  const lib = useQuery<Library>(urls.library);
  const pls = useQuery<{ playlists: Playlist[] }>(urls.playlists);
  const [progress, setProgress] = useState<Record<string, Progress>>({});
  const [addTarget, setAddTarget] = useState<Store["addTarget"]>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [grabOpen, setGrabOpen] = useState(false);
  const [grabPrefill, setGrabPrefill] = useState("");
  const prevStatus = useRef<Map<string, string>>(new Map());
  const toastId = useRef(0);

  const toast = useCallback((t: Omit<Toast, "id">) => {
    const id = ++toastId.current;
    setToasts((ts) => [...ts.slice(-3), { ...t, id }]);
    setTimeout(() => setToasts((ts) => ts.filter((x) => x.id !== id)), 5000);
  }, []);
  const dismiss = useCallback((id: number) => setToasts((ts) => ts.filter((x) => x.id !== id)), []);

  const loadProgress = useCallback(() => {
    fetchQuery<Record<string, Progress>>(urls.progress).then(setProgress).catch(() => {});
  }, []);

  const onJobs = useCallback((next: Job[]) => {
    for (const j of next) {
      const before = prevStatus.current.get(j.id);
      if (before && before !== j.status) {
        if (j.status === "complete") toast({ kind: "ok", title: "Download complete", body: j.title ?? j.url });
        else if (j.status === "error") toast({ kind: "bad", title: "Download failed", body: j.error ?? undefined });
      }
      prevStatus.current.set(j.id, j.status);
    }
    setJobs(next);
  }, [toast]);

  // Live updates from the server (library version, other devices' progress,
  // downloads). If the stream can't be held open (a proxy in the way), poll.
  useEffect(() => {
    let es: EventSource | null = null;
    let poll: ReturnType<typeof setInterval> | undefined;
    let down: ReturnType<typeof setTimeout> | undefined;
    const startPolling = () => {
      if (poll) return;
      const tick = () => {
        if (document.hidden) return;
        api.jobs().then(onJobs).catch(() => {});
        revalidateAll();
      };
      tick();
      poll = setInterval(tick, 15000);
    };
    const stopPolling = () => { clearInterval(poll); poll = undefined; };
    const connect = () => {
      es = new EventSource("/api/events");
      es.onopen = () => { clearTimeout(down); stopPolling(); loadProgress(); };
      es.onerror = () => { clearTimeout(down); down = setTimeout(startPolling, 8000); };
      es.addEventListener("lib", (e) => setVersion(JSON.parse((e as MessageEvent).data).v));
      es.addEventListener("jobs", (e) => onJobs(JSON.parse((e as MessageEvent).data)));
      es.addEventListener("progress", (e) => {
        const p = JSON.parse((e as MessageEvent).data) as { name: string; pos: number; watched: boolean };
        setProgress((m) => ({ ...m, [p.name]: { pos: p.pos, watched: p.watched } }));
      });
    };
    connect();
    loadProgress();
    // phones freeze background tabs: catch up when you come back
    const back = () => { if (!document.hidden) { revalidateAll(); loadProgress(); } };
    document.addEventListener("visibilitychange", back);
    return () => { es?.close(); stopPolling(); clearTimeout(down); document.removeEventListener("visibilitychange", back); };
  }, [onJobs, loadProgress]);

  const refresh = useCallback(() => revalidateAll(), []);

  const grab = useCallback(async (url: string, format: Format, playlist: boolean) => {
    const job = await api.download(url, format, playlist);
    prevStatus.current.set(job.id, job.status);
    setJobs((js) => [job, ...js.filter((j) => j.id !== job.id)]);
    return job;
  }, []);

  const cancel = useCallback(async (id: string) => { await api.cancel(id); }, []);

  const remove = useCallback(async (file: LibraryFile) => {
    await api.remove(file.name);
    refresh();
    toast({ kind: "info", title: "Deleted", body: file.title });
  }, [refresh, toast]);

  const editCollection = useCallback(async (key: string, fields: { title?: string; artist?: string; year?: string }) => {
    const newKey = await api.editCollection(key, fields);
    await fetchQuery(urls.library).catch(() => {});
    toast({ kind: "ok", title: "Saved" });
    return newKey;
  }, [toast]);

  const deleteCollection = useCallback(async (c: Collection) => {
    const r = await api.deleteCollection(c.key);
    setTimeout(refresh, 0);   // after the caller has navigated away from the deleted page
    toast({ kind: "info", title: `Deleted ${c.kind === "singles" ? `${c.artist} · Singles` : c.title}`, body: `${r.deleted} ${r.deleted === 1 ? "file" : "files"}` });
  }, [refresh, toast]);

  const move = useCallback(async (target: { name: string } | { key: string }, to: "Music" | "Videos") => {
    try {
      const r = await api.move(target, to);
      await fetchQuery(urls.library).catch(() => {});
      refresh();
      toast({ kind: "ok", title: `Moved to ${to}` });
      return r.name ?? r.key ?? null;
    } catch (e) {
      toast({ kind: "bad", title: "Couldn't move", body: e instanceof Error ? e.message : String(e) });
      return null;
    }
  }, [refresh, toast]);

  const uploadCollectionCover = useCallback(async (key: string, file: File) => {
    await api.uploadCollectionCover(key, file);
    refresh();
    toast({ kind: "ok", title: "Cover updated" });
  }, [refresh, toast]);

  const setCover = useCallback(async (key: string, name: string) => {
    await api.setCover(key, name);
    refresh();
    toast({ kind: "ok", title: "Cover updated" });
  }, [refresh, toast]);

  // playlists: apply the server's copy right away (optimistic reorders stay smooth)
  const putPlaylist = useCallback((pl: Playlist) => {
    setQueryData<{ playlists: Playlist[] }>(urls.playlists, (old) => ({
      playlists: [pl, ...(old?.playlists ?? []).filter((x) => x.id !== pl.id)].sort((a, b) => b.updated.localeCompare(a.updated)),
    }));
    setQueryData<{ playlist: Playlist }>(urls.playlist(pl.id), (old) => old && ({
      playlist: { ...pl, files: pl.items.map((n) => old.playlist.files?.find((f) => f.name === n)).filter((f): f is LibraryFile => !!f) },
    }));
    return pl;
  }, []);
  const createPlaylist = useCallback(async (title: string, items: string[] = [], description = "") =>
    putPlaylist(await api.createPlaylist(title, items, description)), [putPlaylist]);
  const updatePlaylist = useCallback(async (id: string, patch: PlaylistPatch) => {
    if (patch.items) {   // optimistic: drag-reordering shouldn't wait for the server
      const order = patch.items;
      setQueryData<{ playlist: Playlist }>(urls.playlist(id), (old) => old && ({
        playlist: {
          ...old.playlist, items: order, count: order.length,
          files: order.map((n) => old.playlist.files?.find((f) => f.name === n)).filter((f): f is LibraryFile => !!f),
        },
      }));
    }
    try {
      const pl = putPlaylist(await api.updatePlaylist(id, patch));
      fetchQuery(urls.playlist(id)).catch(() => {});   // new items' details
      return pl;
    } catch (e) {
      refresh();
      throw e;
    }
  }, [putPlaylist, refresh]);
  const deletePlaylist = useCallback(async (id: string) => {
    await api.deletePlaylist(id);
    setQueryData<{ playlists: Playlist[] }>(urls.playlists, (old) => ({ playlists: (old?.playlists ?? []).filter((p) => p.id !== id) }));
    toast({ kind: "info", title: "Playlist deleted" });
  }, [toast]);
  const uploadPlaylistCover = useCallback(async (id: string, file: File) =>
    putPlaylist(await api.uploadPlaylistCover(id, file)), [putPlaylist]);

  const noteProgress = useCallback((name: string, p: Partial<Progress>) => {
    setProgress((m) => ({ ...m, [name]: { ...(m[name] ?? { pos: 0, watched: false }), ...p } }));
  }, []);
  const setWatched = useCallback(async (f: LibraryFile, watched: boolean) => {
    noteProgress(f.name, { watched, pos: 0 });
    await api.progress(f.name, watched ? { watched } : { watched, pos: 0 });
  }, [noteProgress]);

  const openAddToPlaylist = useCallback((names: string[], label?: string) =>
    setAddTarget({ names, label: label ?? `${names.length} ${names.length === 1 ? "item" : "items"}` }), []);
  const closeAddToPlaylist = useCallback(() => setAddTarget(null), []);

  const openGrab = useCallback((prefill = "") => { setGrabPrefill(prefill); setGrabOpen(true); }, []);
  const closeGrab = useCallback(() => setGrabOpen(false), []);

  return (
    <Ctx.Provider value={{
      library: lib.data ?? null, jobs, activeCount: jobs.filter(isActive).length, refresh,
      grab, cancel, remove, editCollection, setCover, uploadCollectionCover, deleteCollection, move,
      progress, noteProgress, setWatched,
      playlists: pls.data?.playlists ?? [], playlistsLoaded: !!pls.data,
      createPlaylist, updatePlaylist, deletePlaylist, uploadPlaylistCover, addTarget, openAddToPlaylist, closeAddToPlaylist,
      toasts, toast, dismiss, grabOpen, grabPrefill, openGrab, closeGrab,
    }}>
      {children}
    </Ctx.Provider>
  );
}

export function useStore() {
  const s = useContext(Ctx);
  if (!s) throw new Error("useStore outside provider");
  return s;
}

/** Watched videos in a collection (null when none yet). */
export function useWatchedCount(key: string): number | null {
  const { library } = useStore();
  return library?.watched[key] || null;
}
