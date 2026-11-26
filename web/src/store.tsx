import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api, isActive, type Collection, type Format, type Job, type LibraryFile, type Playlist, type PlaylistPatch } from "./api";

export interface Progress { pos: number; watched: boolean }

export interface Toast { id: number; kind: "ok" | "bad" | "info"; title: string; body?: string }

interface Store {
  files: LibraryFile[];
  collections: Collection[];
  filesLoaded: boolean;
  dir: string;
  jobs: Job[];
  activeCount: number;
  refreshFiles: () => Promise<void>;
  grab: (url: string, format: Format, playlist: boolean) => Promise<Job>;
  cancel: (id: string) => Promise<void>;
  remove: (file: LibraryFile) => Promise<void>;
  editCollection: (key: string, fields: { title?: string; artist?: string; year?: string }) => Promise<string>;
  setCover: (key: string, name: string) => Promise<void>;
  uploadCollectionCover: (key: string, file: File) => Promise<void>;
  deleteCollection: (c: Collection) => Promise<void>;
  /** resume position + watched, by library name */
  progress: Record<string, Progress>;
  noteProgress: (name: string, p: Partial<Progress>) => void;
  setWatched: (f: LibraryFile, watched: boolean) => Promise<void>;
  playlists: Playlist[];
  createPlaylist: (title: string, items?: string[], description?: string) => Promise<Playlist>;
  updatePlaylist: (id: string, patch: PlaylistPatch) => Promise<Playlist>;
  deletePlaylist: (id: string) => Promise<void>;
  uploadPlaylistCover: (id: string, file: File) => Promise<Playlist>;
  /** "Add to playlist" picker for these library names */
  addTarget: string[] | null;
  openAddToPlaylist: (names: string[]) => void;
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
  const [files, setFiles] = useState<LibraryFile[]>([]);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [playlists, setPlaylists] = useState<Playlist[]>([]);
  const [progress, setProgress] = useState<Record<string, Progress>>({});
  const [addTarget, setAddTarget] = useState<string[] | null>(null);
  const [filesLoaded, setFilesLoaded] = useState(false);
  const [dir, setDir] = useState("");
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

  // One refresh at a time; a call made while one runs gets one more right after it.
  const busy = useRef<Promise<void> | null>(null);
  const again = useRef(false);
  const refreshFiles = useCallback(async (): Promise<void> => {
    if (busy.current) {
      again.current = true;
      return busy.current;
    }
    const run = async () => {
      do {
        again.current = false;
        try {
          const [r, p] = await Promise.all([api.files(), api.progressAll()]);
          if (r) {
            setFiles(r.files);
            setCollections(r.collections ?? []);
            setPlaylists(r.playlists ?? []);
            setDir(r.dir);
          }
          if (p) setProgress(p);
        } catch { /* server hiccup; next poll retries */ }
      } while (again.current);
      setFilesLoaded(true);
    };
    busy.current = run().finally(() => { busy.current = null; });
    return busy.current;
  }, []);

  // Jobs: poll fast while something is running, slowly otherwise.
  const activeCount = jobs.filter(isActive).length;
  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const next = await api.jobs();
        if (stop) return;
        let finished = false;
        for (const j of next) {
          const before = prevStatus.current.get(j.id);
          if (before && before !== j.status) {
            if (j.status === "complete") {
              finished = true;
              toast({ kind: "ok", title: "Download complete", body: j.title ?? j.url });
            } else if (j.status === "error") {
              finished = true;
              toast({ kind: "bad", title: "Download failed", body: j.error ?? undefined });
            }
          }
          // a file just landed mid-playlist → refresh library too
          const prevFiles = prevStatus.current.get(j.id + ":n");
          if (prevFiles && Number(prevFiles) < j.files.length) finished = true;
          prevStatus.current.set(j.id, j.status);
          prevStatus.current.set(j.id + ":n", String(j.files.length));
        }
        setJobs(next);
        if (finished) refreshFiles();
      } catch { /* ignore */ }
      if (!stop) timer = setTimeout(tick, document.hidden ? 5000 : activeRef.current ? 1200 : 4000);
    };
    tick();
    return () => { stop = true; clearTimeout(timer); };
  }, [refreshFiles, toast]);
  const activeRef = useRef(0);
  activeRef.current = activeCount;

  useEffect(() => {
    refreshFiles();
    const t = setInterval(() => { if (!document.hidden) refreshFiles(); }, 8000);
    const back = () => { if (!document.hidden) refreshFiles(); };
    document.addEventListener("visibilitychange", back);
    return () => { clearInterval(t); document.removeEventListener("visibilitychange", back); };
  }, [refreshFiles]);

  const grab = useCallback(async (url: string, format: Format, playlist: boolean) => {
    const job = await api.download(url, format, playlist);
    prevStatus.current.set(job.id, job.status);
    setJobs((js) => [job, ...js]);
    return job;
  }, []);

  const cancel = useCallback(async (id: string) => {
    await api.cancel(id);
  }, []);

  const remove = useCallback(async (file: LibraryFile) => {
    await api.remove(file.name);
    setFiles((fs) => fs.filter((f) => f.name !== file.name));
    toast({ kind: "info", title: "Deleted", body: file.title });
  }, [toast]);

  const editCollection = useCallback(async (key: string, fields: { title?: string; artist?: string; year?: string }) => {
    const newKey = await api.editCollection(key, fields);
    await refreshFiles();
    toast({ kind: "ok", title: "Saved" });
    return newKey;
  }, [refreshFiles, toast]);

  const deleteCollection = useCallback(async (c: Collection) => {
    const r = await api.deleteCollection(c.key);
    setTimeout(refreshFiles, 0);   // after the caller has navigated away from the deleted page
    toast({ kind: "info", title: `Deleted ${c.kind === "singles" ? `${c.artist} · Singles` : c.title}`, body: `${r.deleted} ${r.deleted === 1 ? "file" : "files"}` });
  }, [refreshFiles, toast]);

  const uploadCollectionCover = useCallback(async (key: string, file: File) => {
    await api.uploadCollectionCover(key, file);
    await refreshFiles();
    toast({ kind: "ok", title: "Cover updated" });
  }, [refreshFiles, toast]);

  const setCover = useCallback(async (key: string, name: string) => {
    await api.setCover(key, name);
    await refreshFiles();
    toast({ kind: "ok", title: "Cover updated" });
  }, [refreshFiles, toast]);

  // playlists: apply the server's copy right away (optimistic reorders stay smooth)
  const putPlaylist = useCallback((pl: Playlist) => {
    setPlaylists((ps) => [pl, ...ps.filter((x) => x.id !== pl.id)].sort((a, b) => b.updated.localeCompare(a.updated)));
    return pl;
  }, []);
  const createPlaylist = useCallback(async (title: string, items: string[] = [], description = "") =>
    putPlaylist(await api.createPlaylist(title, items, description)), [putPlaylist]);
  const updatePlaylist = useCallback(async (id: string, patch: PlaylistPatch) => {
    if (patch.items) {   // optimistic: drag-reordering shouldn't wait for the server
      setPlaylists((ps) => ps.map((p) => (p.id === id ? { ...p, items: patch.items! } : p)));
    }
    try {
      return putPlaylist(await api.updatePlaylist(id, patch));
    } catch (e) {
      refreshFiles();
      throw e;
    }
  }, [putPlaylist, refreshFiles]);
  const deletePlaylist = useCallback(async (id: string) => {
    await api.deletePlaylist(id);
    setPlaylists((ps) => ps.filter((p) => p.id !== id));
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

  const openAddToPlaylist = useCallback((names: string[]) => setAddTarget(names), []);
  const closeAddToPlaylist = useCallback(() => setAddTarget(null), []);

  const openGrab = useCallback((prefill = "") => { setGrabPrefill(prefill); setGrabOpen(true); }, []);
  const closeGrab = useCallback(() => setGrabOpen(false), []);

  return (
    <Ctx.Provider value={{
      files, collections, filesLoaded, dir, jobs, activeCount, refreshFiles, grab, cancel, remove, editCollection, setCover, uploadCollectionCover, deleteCollection,
      progress, noteProgress, setWatched,
      playlists, createPlaylist, updatePlaylist, deletePlaylist, uploadPlaylistCover, addTarget, openAddToPlaylist, closeAddToPlaylist,
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

/** "4 of 12 watched" for a video collection (null for music / nothing watched yet). */
export function useWatchedCount(key: string): number | null {
  const { files, progress } = useStore();
  let n = 0, any = false;
  for (const f of files) {
    if (f.collection !== key || f.kind !== "video") continue;
    any = true;
    if (progress[f.name]?.watched) n++;
  }
  return any && n > 0 ? n : null;
}
