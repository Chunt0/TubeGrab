import { useEffect, useReducer, useRef, useState, useSyncExternalStore } from "react";
import { HttpError } from "../api";

/**
 * Query cache. Every GET the app shows is kept in memory and on the device
 * (IndexedDB), so pages appear instantly — even right after opening the app —
 * and are then re-checked with the server. Re-checks send the ETag the server
 * gave last time, so "nothing changed" costs no data.
 *
 * The server pushes a library version (see store.tsx); when it changes, every
 * query on screen re-checks itself.
 */

interface Entry<T = unknown> { data: T; etag: string | null; at: number }

const mem = new Map<string, Entry>();
const subs = new Map<string, Set<() => void>>();
const inflight = new Map<string, Promise<unknown>>();
const hydrating = new Map<string, Promise<void>>();

// ---- IndexedDB (one object store: url → Entry)
const DB_NAME = "tubegrab-cache";
const STORE = "q";
const MAX_ENTRIES = 400;
let dbp: Promise<IDBDatabase | null> | null = null;
function idb(): Promise<IDBDatabase | null> {
  if (!dbp) {
    dbp = new Promise((resolve) => {
      try {
        const r = indexedDB.open(DB_NAME, 1);
        r.onupgradeneeded = () => r.result.createObjectStore(STORE);
        r.onsuccess = () => resolve(r.result);
        r.onerror = () => resolve(null);
      } catch { resolve(null); }
    });
  }
  return dbp;
}
async function idbGet(key: string): Promise<Entry | undefined> {
  const db = await idb();
  if (!db) return undefined;
  return new Promise((resolve) => {
    try {
      const q = db.transaction(STORE).objectStore(STORE).get(key);
      q.onsuccess = () => resolve(q.result as Entry | undefined);
      q.onerror = () => resolve(undefined);
    } catch { resolve(undefined); }
  });
}
let writes = 0;
async function idbPut(key: string, e: Entry) {
  const db = await idb();
  if (!db) return;
  try { db.transaction(STORE, "readwrite").objectStore(STORE).put(e, key); } catch { /* quota: fine */ }
  if (++writes % 50 === 0) prune(db);
}
/** keep the newest MAX_ENTRIES (the library summary is re-written often, so it stays) */
function prune(db: IDBDatabase) {
  try {
    const store = db.transaction(STORE, "readwrite").objectStore(STORE);
    const keys: { k: IDBValidKey; at: number }[] = [];
    store.openCursor().onsuccess = (ev) => {
      const cur = (ev.target as IDBRequest<IDBCursorWithValue | null>).result;
      if (cur) { keys.push({ k: cur.key, at: (cur.value as Entry).at }); cur.continue(); return; }
      if (keys.length <= MAX_ENTRIES) return;
      keys.sort((a, b) => b.at - a.at).slice(MAX_ENTRIES).forEach(({ k }) => store.delete(k));
    };
  } catch { /* ok */ }
}

function emit(url: string) { subs.get(url)?.forEach((f) => f()); }
function subscribe(url: string, f: () => void) {
  let s = subs.get(url);
  if (!s) subs.set(url, (s = new Set()));
  s.add(f);
  return () => { s!.delete(f); };
}

/** Load what the device has for `url` (once), before the network answers. */
function hydrate(url: string): Promise<void> {
  if (mem.has(url)) return Promise.resolve();
  let h = hydrating.get(url);
  if (!h) {
    h = idbGet(url).then((e) => {
      if (e && !mem.has(url)) { mem.set(url, e); emit(url); }
    });
    hydrating.set(url, h);
  }
  return h;
}

/** Fetch (or re-check) a query; concurrent calls share one request. */
export function fetchQuery<T>(url: string): Promise<T> {
  const running = inflight.get(url);
  if (running) return running as Promise<T>;
  const p = (async () => {
    await hydrate(url);
    const have = mem.get(url) as Entry<T> | undefined;
    const r = await fetch(url, { headers: have?.etag ? { "If-None-Match": have.etag } : {}, cache: "no-store" });
    if (r.status === 304 && have) {
      have.at = Date.now();
      return have.data;
    }
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      if (r.status === 404) { mem.delete(url); emit(url); }
      throw new HttpError(body.error || r.statusText || "Request failed", r.status);
    }
    const e: Entry<T> = { data: body as T, etag: r.headers.get("ETag"), at: Date.now() };
    mem.set(url, e);
    emit(url);
    idbPut(url, e);
    return e.data;
  })().finally(() => inflight.delete(url));
  inflight.set(url, p);
  return p;
}

/** What's cached for `url` right now, if anything. */
export const peekQuery = <T,>(url: string) => mem.get(url)?.data as T | undefined;

/** Change cached data locally (optimistic updates); `undefined` drops it. */
export function setQueryData<T>(url: string, update: (old: T | undefined) => T | undefined) {
  const old = mem.get(url) as Entry<T> | undefined;
  const data = update(old?.data);
  if (data === undefined) mem.delete(url);
  else mem.set(url, { data, etag: null, at: Date.now() });
  emit(url);
}

// ---- server version: when it changes, everything on screen re-checks
let version = "";
const vsubs = new Set<() => void>();
export function setVersion(v: string) {
  if (v === version) return;
  version = v;
  vsubs.forEach((f) => f());
}
/** Re-check every query on screen now (after your own edits). */
export const revalidateAll = () => setVersion(`${version.split("~")[0]}~${Date.now()}`);
const useVersion = () => useSyncExternalStore((f) => { vsubs.add(f); return () => { vsubs.delete(f); }; }, () => version);

/**
 * Data for `url` (null: nothing to fetch). Shows what the device has at once,
 * then the server's answer. `keep`: while a new url loads, keep showing the
 * previous data (search as you type, "load more").
 */
export function useQuery<T>(url: string | null, opts: { keep?: boolean } = {}) {
  const ver = useVersion();
  const [, rerender] = useReducer((x: number) => x + 1, 0);
  const [error, setError] = useState<HttpError | null>(null);
  const last = useRef<T | undefined>(undefined);

  useEffect(() => (url ? subscribe(url, rerender) : undefined), [url]);
  useEffect(() => {
    if (!url) return;
    let live = true;
    setError(null);
    fetchQuery<T>(url).catch((e) => { if (live) setError(e instanceof HttpError ? e : new HttpError(String(e), 0)); });
    return () => { live = false; };
  }, [url, ver]);

  const entry = url ? (mem.get(url) as Entry<T> | undefined) : undefined;
  if (entry) last.current = entry.data;
  const data = entry ? entry.data : opts.keep ? last.current : undefined;
  return { data, error, loading: !!url && !entry && !error, fresh: !!entry };
}

/**
 * A long list, loaded as you scroll: asks for the first `step` items, and for
 * `step` more each time the end comes near (one request per step, cached).
 */
export function usePaged<T>(base: string | null, step = 60) {
  const [n, setN] = useState(step);
  useEffect(() => setN(step), [base, step]);
  const url = base ? `${base}${base.includes("?") ? "&" : "?"}limit=${n}` : null;
  const { data, error, fresh } = useQuery<{ total: number; files: T[] }>(url, { keep: true });
  const items = data?.files ?? [];
  const total = data?.total ?? 0;
  const more = fresh && items.length < total;
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = end.current;
    if (!el || !more) return;
    const io = new IntersectionObserver((es) => { if (es[0]?.isIntersecting) setN((x) => x + step); }, { rootMargin: "1200px 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, [more, n, step]);
  return {
    items, total, error,
    loaded: data !== undefined,
    sentinel: more ? <div ref={end} aria-hidden className="h-px" /> : null,
  };
}
