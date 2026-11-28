import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Maximize2, Pause, Play, SkipBack, SkipForward, X } from "lucide-react";
import { HttpError, api, mediaUrl, resumable, thumbUrl, urls, type LibraryFile, type WatchData } from "./api";
import { useStore } from "./store";
import { fetchQuery, useQuery } from "./lib/cache";
import { useRouter, watchPath } from "./router";
import { cleanChannel, cx, duration } from "./lib/format";
import { useMediaState } from "./lib/media";

/**
 * One persistent <video> element for the whole app (audio plays in it too,
 * showing the cover art as poster). The Watch page "attaches" it to its slot;
 * leaving the page while something plays moves it into native
 * picture-in-picture (video) or a corner mini player (audio, or when the
 * browser refuses PiP). Moving the element in a single DOM operation keeps
 * playback (and an open PiP window) alive.
 */
type Mode = "idle" | "page" | "mini" | "pip";

interface PlayerCtx {
  media: HTMLVideoElement;
  file: LibraryFile | null;
  /** next in queue / previous (restart if >3s in, else album order, else history) */
  next: () => void;
  prev: () => void;
  hasNext: boolean;
  /** playlist being played through (its order drives next/previous), if any */
  list: string | null;
  setList: (id: string | null) => void;
  mode: Mode;
  autoplay: boolean;
  setAutoplay: (v: boolean) => void;
  /** `start`: seconds to begin at (else resume where you stopped) · `play` false: load it paused */
  open: (file: LibraryFile, start?: number | null, play?: boolean) => void;
  /** playback speed for videos / long-form audio (music always plays at 1×) */
  speed: number;
  setSpeed: (v: number) => void;
  attach: (slot: HTMLElement) => void;
  detach: (slot: HTMLElement) => void;
  close: () => void;
}

const Ctx = createContext<PlayerCtx | null>(null);
const AUTOPLAY_KEY = "tubegrab.autoplay";
const SPEED_KEY = "tubegrab.speed";
const readSpeed = () => { try { return Number(localStorage.getItem(SPEED_KEY)) || 1; } catch { return 1; } };
const readAutoplay = () => { try { return localStorage.getItem(AUTOPLAY_KEY) !== "0"; } catch { return true; } };
/** what's playing, so a reload brings it back (paused, where it was) */
const NOW_KEY = "tubegrab.nowplaying";
type NowPlaying = { name: string; list: string | null; pos: number };
const readNow = (): NowPlaying | null => { try { return JSON.parse(localStorage.getItem(NOW_KEY) ?? "null"); } catch { return null; } };
const writeNow = (n: NowPlaying | null) => {
  try { if (n) localStorage.setItem(NOW_KEY, JSON.stringify(n)); else localStorage.removeItem(NOW_KEY); } catch { /* ok */ }
};

function createMedia(): HTMLVideoElement {
  const v = document.createElement("video");
  v.controls = true;
  v.playsInline = true;
  v.preload = "metadata";
  v.setAttribute("autopictureinpicture", ""); // Chrome: auto-PiP when the tab is hidden
  v.className = "block size-full bg-black object-contain";
  return v;
}

export function PlayerProvider({ children }: { children: ReactNode }) {
  const { progress, noteProgress, setWatched } = useStore();
  const [speed, setSpeedState] = useState(readSpeed);
  const [list, setListState] = useState<string | null>(null);
  const { navigate, path } = useRouter();
  const [el] = useState(createMedia);
  const [file, setFile] = useState<LibraryFile | null>(null);
  const [mode, setMode] = useState<Mode>("idle");
  const [autoplay, setAutoplayState] = useState(readAutoplay);
  const parking = useRef<HTMLDivElement>(null);
  const slotRef = useRef<HTMLElement | null>(null);

  // what plays around the current file (shared with the Watch page through the cache)
  const queueUrl = file ? urls.watch(file.name, list) : null;
  const { data: queueData, error: queueError } = useQuery<WatchData>(queueUrl);
  const queue = queueData && queueData.file.name === file?.name ? queueData : null;

  // latest values for event handlers
  const live = useRef({ file, mode, autoplay, list, progress, speed, queue });
  live.current = { file, mode, autoplay, list, progress, speed, queue };
  const setList = useCallback((id: string | null) => { live.current.list = id; setListState(id); }, []);

  // update the ref synchronously too: PiP events can fire before React re-renders
  const changeMode = useCallback((m: Mode) => { live.current.mode = m; setMode(m); }, []);

  const setAutoplay = useCallback((v: boolean) => {
    setAutoplayState(v);
    try { localStorage.setItem(AUTOPLAY_KEY, v ? "1" : "0"); } catch { /* ok */ }
  }, []);

  // what was playing before a reload (used once, by the first file opened or the restore below)
  const restored = useRef(readNow());
  const lastNow = useRef(0);
  const saveNow = useCallback(() => {
    const f = live.current.file;
    if (!f || el.ended) return;   // a finished file isn't brought back
    lastNow.current = Date.now();
    writeNow({ name: f.name, list: live.current.list, pos: Math.floor(el.currentTime) });
  }, [el]);

  // what played before, for "previous" outside albums/playlists
  const hist = useRef<string[]>([]);
  const goingBack = useRef(false);

  // ---- progress: save where you are (every 10 s, on pause / switch / leave)
  const lastSave = useRef(0);
  const marked = useRef<string | null>(null);   // finished (watched) this session
  const counted = useRef<string | null>(null);  // this listen already counted as a play
  const saveProgress = useCallback((beacon = false) => {
    const f = live.current.file;
    if (!f || !resumable(f) || !el.currentTime || marked.current === f.name) return;
    const pos = el.currentTime;
    lastSave.current = Date.now();
    if (beacon) api.progressBeacon(f.name, pos);
    else api.progress(f.name, { pos }).catch(() => {});
    noteProgress(f.name, { pos });
  }, [el, noteProgress]);

  const setSpeed = useCallback((v: number) => {
    el.playbackRate = el.defaultPlaybackRate = v;
    setSpeedState(v);
    live.current.speed = v;
    try { localStorage.setItem(SPEED_KEY, String(v)); } catch { /* ok */ }
  }, [el]);

  const open = useCallback((f: LibraryFile, start?: number | null, play = true) => {
    const cur = live.current.file;
    const back = goingBack.current;
    goingBack.current = false;
    if (cur?.name === f.name) {
      if (start != null) el.currentTime = start;
      return;
    }
    if (cur) saveProgress();
    if (cur && !back) hist.current = [...hist.current.slice(-49), cur.name];
    setFile(f);
    marked.current = null;
    el.src = mediaUrl(f.name);
    // speed is for lectures & long listens; songs stay at 1×
    el.playbackRate = el.defaultPlaybackRate = f.section === "Music" ? 1 : live.current.speed;
    // resume where you stopped (unless finished, or nearly at the very start/end)
    const p = live.current.progress[f.name];
    const r = restored.current;
    restored.current = null;
    const at = start ?? (r?.name === f.name && r.pos > 1 ? r.pos : null)
      ?? (resumable(f) && p && !p.watched && p.pos > 15 && (!f.duration || p.pos < f.duration - 20) ? p.pos : null);
    writeNow({ name: f.name, list: live.current.list, pos: at ?? 0 });
    if (at) el.addEventListener("loadedmetadata", () => { el.currentTime = at; }, { once: true });
    el.poster = thumbUrl(f.name, f.art, true);
    // PiP needs a video track; keep audio out of it
    el.disablePictureInPicture = f.kind === "audio";
    if (play) el.play().catch(() => { /* autoplay blocked: user presses play */ });
    if ("mediaSession" in navigator) {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: f.title,
        artist: cleanChannel(f.uploader),
        album: f.album ?? f.collection.split("/").pop() ?? "",
        artwork: [{ src: thumbUrl(f.name, f.art, true) }],
      });
    }
  }, [el, saveProgress]);

  const close = useCallback(() => {
    saveProgress();
    if (document.pictureInPictureElement === el) document.exitPictureInPicture().catch(() => {});
    el.pause();
    el.removeAttribute("src");
    el.removeAttribute("poster");
    el.load();
    parking.current?.appendChild(el);
    setFile(null);
    changeMode("idle");
    writeNow(null);
    if ("mediaSession" in navigator) navigator.mediaSession.metadata = null;
  }, [el, changeMode]);

  const attach = useCallback((slot: HTMLElement) => {
    slotRef.current = slot;
    slot.appendChild(el);
    changeMode("page");
    if (document.pictureInPictureElement === el) document.exitPictureInPicture().catch(() => {});
  }, [el, changeMode]);

  const detach = useCallback((slot: HTMLElement) => {
    if (slotRef.current !== slot) return;
    slotRef.current = null;
    const playing = !el.paused && !el.ended;
    parking.current?.appendChild(el); // same-task move: playback continues
    if (!playing) { close(); return; }
    if (document.pictureInPictureElement === el) { changeMode("pip"); return; }   // PiP opened from the page
    changeMode("mini");
    if (live.current.file?.kind === "video" && document.pictureInPictureEnabled && document.pictureInPictureElement !== el) {
      el.requestPictureInPicture().catch(() => { /* no user activation (e.g. Back button): stay in mini player */ });
    }
  }, [el, close, changeMode]);

  const go = useCallback((f: LibraryFile, back = false) => {
    goingBack.current = back;
    // start it now, then follow on the page: waiting for the next page to load first
    // stalls a locked phone (the browser suspends the page once nothing is playing)
    open(f);
    if (live.current.mode === "page") navigate(watchPath(f.name, live.current.list));
  }, [navigate, open]);

  /** the queue for the current file (waits for it if it's still loading) */
  const currentQueue = useCallback(async () => {
    const { file: f, list: l, queue: q } = live.current;
    if (!f) return null;
    return q ?? fetchQuery<WatchData>(urls.watch(f.name, l)).catch(() => null);
  }, []);

  const next = useCallback(() => {
    const q = live.current.queue;   // loaded while this one played: switch within the "ended" event
    if (q) { if (q.next[0]) go(q.next[0]); return; }
    currentQueue().then((q) => { const n = q?.next[0]; if (n) go(n); });
  }, [go, currentQueue]);

  const prev = useCallback(() => {
    if (!live.current.file) return;
    if (el.currentTime > 3) { el.currentTime = 0; return; }
    currentQueue().then(async (q) => {
      if (q?.prev) { go(q.prev, true); return; }
      while (hist.current.length) {   // outside albums / playlists: what played before
        const name = hist.current[hist.current.length - 1];
        hist.current = hist.current.slice(0, -1);
        const w = await fetchQuery<WatchData>(urls.watch(name)).catch(() => null);
        if (w) { go(w.file, true); return; }
      }
      el.currentTime = 0;
    });
  }, [el, go, currentQueue]);

  useEffect(() => {
    const onTime = () => {
      const f = live.current.file;
      if (!f) return;
      // a play: half of it or 4 minutes, whichever comes first (once per listen; again after a restart)
      if (counted.current === f.name && el.currentTime < 5) counted.current = null;
      else if (counted.current !== f.name && el.duration >= 30 && el.currentTime >= Math.min(el.duration / 2, 240)) {
        counted.current = f.name;
        api.played(f.name).catch(() => {});
      }
      if (Date.now() - lastNow.current > 5_000) saveNow();
      if (!resumable(f)) return;
      if (el.duration && el.currentTime / el.duration >= 0.92 && marked.current !== f.name && !live.current.progress[f.name]?.watched) {
        marked.current = f.name;
        setWatched(f, true).catch(() => {});
      } else if (Date.now() - lastSave.current > 10_000) saveProgress();
    };
    const onPause = () => { saveProgress(); saveNow(); };
    const onHide = () => { saveNow(); if (document.visibilityState === "hidden") saveProgress(true); };
    el.addEventListener("timeupdate", onTime);
    el.addEventListener("pause", onPause);
    document.addEventListener("visibilitychange", onHide);
    addEventListener("pagehide", onHide);
    return () => {
      el.removeEventListener("timeupdate", onTime);
      el.removeEventListener("pause", onPause);
      document.removeEventListener("visibilitychange", onHide);
      removeEventListener("pagehide", onHide);
    };
  }, [el, saveProgress, saveNow, setWatched]);

  // after a reload: bring back what was playing, paused, in the mini player
  // (on a Watch page, that page opens its file, and takes the position if it's the same one)
  useEffect(() => {
    const r = restored.current;
    if (!r || path.startsWith("/watch/")) return;
    fetchQuery<WatchData>(urls.watch(r.name, r.list)).then((w) => {
      if (live.current.file || restored.current !== r) return;   // something else started meanwhile
      setList(w.playlist ? r.list : null);
      parking.current?.appendChild(el);
      open(w.file, null, false);
      changeMode("mini");
    }).catch(() => { if (restored.current === r) { restored.current = null; writeNow(null); } });
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  // element events
  useEffect(() => {
    const onEnter = () => { if (live.current.mode !== "page") changeMode("pip"); };
    const onLeave = () => {
      if (live.current.mode === "page") return;
      // PiP closed: "back to tab" keeps playing → mini player; the close button pauses → stop
      if (el.paused) close(); else changeMode("mini");
    };
    const onEnded = () => {
      writeNow(null);   // finished; the next one (if any) saves itself
      if (live.current.autoplay) next();
    };
    el.addEventListener("enterpictureinpicture", onEnter);
    el.addEventListener("leavepictureinpicture", onLeave);
    el.addEventListener("ended", onEnded);
    return () => {
      el.removeEventListener("enterpictureinpicture", onEnter);
      el.removeEventListener("leavepictureinpicture", onLeave);
      el.removeEventListener("ended", onEnded);
    };
  }, [el, close, next, changeMode]);

  // OS media controls + Chrome's automatic PiP when switching tabs
  useEffect(() => {
    if (!("mediaSession" in navigator)) return;
    const set = (action: string, handler: (() => void) | null) => {
      try { navigator.mediaSession.setActionHandler(action as MediaSessionAction, handler); } catch { /* unsupported */ }
    };
    set("play", () => el.play());
    set("pause", () => el.pause());
    set("nexttrack", next);
    set("previoustrack", prev);
    set("seekbackward", () => { el.currentTime = Math.max(0, el.currentTime - 10); });
    set("seekforward", () => { el.currentTime = Math.min(el.duration || Infinity, el.currentTime + 10); });
    set("seekto", ((d: MediaSessionActionDetails) => { if (d.seekTime != null) el.currentTime = d.seekTime; }) as unknown as () => void);
    set("enterpictureinpicture", () => {
      if (live.current.file?.kind === "video") el.requestPictureInPicture().catch(() => {});
    });
  }, [el, next, prev]);

  // the playing file was deleted
  useEffect(() => {
    if (queueError instanceof HttpError && queueError.status === 404) close();
  }, [queueError, close]);

  const showMini = !!(mode === "mini" && file);
  const audio = file?.kind === "audio";
  // the Watch page draws its own controls; the desktop video card keeps the native
  // ones; the phone bar and the desktop music bar have their own
  useEffect(() => {
    el.controls = showMini && !audio && !matchMedia("(width < 40rem)").matches;
    if (showMini) document.documentElement.dataset.mini = audio ? "bar" : "card";
    else delete document.documentElement.dataset.mini;
  }, [el, showMini, audio]);
  const hasNext = !!queue?.next.length;
  const openPage = () => file && navigate(watchPath(file.name, list));

  return (
    <Ctx.Provider value={{ media: el, file, next, prev, hasNext, list, setList, speed, setSpeed, mode, autoplay, setAutoplay, open, attach, detach, close }}>
      {children}
      {/* parking spot / mini player; stays in the DOM so PiP keeps a live element.
          phone: slim bar above the tab bar · desktop: music bar along the bottom, or a corner card for video */}
      <div
        className={cx(
          "fixed z-50 overflow-hidden border-line-strong bg-surface",
          !showMini ? "pointer-events-none bottom-0 right-0 size-px opacity-0"
            : cx("inset-x-2 bottom-[calc(var(--tabbar)+0.5rem)] flex h-16 items-center rounded-sm border animate-fade",
              audio ? "sm:inset-x-0 sm:bottom-0 sm:h-[4.5rem] sm:rounded-none sm:border-x-0 sm:border-b-0 sm:bg-ink/95 sm:px-4 sm:backdrop-blur"
                : "sm:inset-x-auto sm:bottom-4 sm:right-4 sm:block sm:h-auto sm:w-[360px]"),
        )}
      >
        <div ref={parking}
          className={cx("shrink-0 bg-black", !showMini ? "aspect-video" : audio ? "aspect-square h-full sm:h-12 sm:rounded-sm sm:overflow-hidden" : "aspect-video h-full sm:h-auto sm:w-full")} />
        {showMini && file && (
          <MiniControls el={el} file={file} audio={audio} hasNext={hasNext} next={next} prev={prev} onOpen={openPage} onClose={close} />
        )}
      </div>
    </Ctx.Provider>
  );
}

/** Seek bar for the music bar (click or drag). */
function Seek({ el, frac, dur }: { el: HTMLVideoElement; frac: number; dur: number }) {
  const [drag, setDrag] = useState<number | null>(null);
  const at = (e: React.PointerEvent) => {
    const r = e.currentTarget.getBoundingClientRect();
    return Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
  };
  return (
    <div
      onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); setDrag(at(e)); }}
      onPointerMove={(e) => { if (drag != null) setDrag(at(e)); }}
      onPointerUp={(e) => { if (drag != null && dur) el.currentTime = at(e) * dur; setDrag(null); }}
      onPointerCancel={() => setDrag(null)}
      className="group/seek relative flex h-4 flex-1 cursor-pointer touch-none items-center"
    >
      <div className="h-[3px] w-full bg-white/15 group-hover/seek:h-[4px]">
        <div className="h-full bg-fg" style={{ width: `${(drag ?? frac) * 100}%` }} />
      </div>
    </div>
  );
}

function MiniControls({ el, file, audio, hasNext, next, prev, onOpen, onClose }: {
  el: HTMLVideoElement; file: LibraryFile; audio: boolean; hasNext: boolean;
  next: () => void; prev: () => void; onOpen: () => void; onClose: () => void;
}) {
  const ms = useMediaState(el);   // isolated here so time updates don't re-render the app
  const frac = ms.dur ? ms.time / ms.dur : 0;
  const toggle = () => (el.paused ? el.play().catch(() => {}) : el.pause());
  const who = file.artist || cleanChannel(file.uploader);
  const PlayIcon = ms.paused ? Play : Pause;
  const btn = "grid place-items-center text-faint transition-colors hover:text-fg disabled:opacity-30";
  return (
    <>
      {/* thin progress line: phone bar + desktop video card */}
      <div className={cx("absolute inset-x-0 top-0 h-[2px] bg-white/10", audio && "sm:hidden")}>
        <div className="h-full bg-fg/80" style={{ width: `${frac * 100}%` }} />
      </div>

      {/* compact row */}
      <div className={cx("flex min-w-0 flex-1 items-center gap-0.5 py-1.5 pl-3 pr-1", audio ? "sm:hidden" : "sm:border-t sm:border-line")}>
        <button onClick={onOpen} className="min-w-0 flex-1 text-left">
          <div className="truncate text-[13px] text-fg/90 hover:text-white sm:text-xs">{file.title}</div>
          <div className="truncate text-[11.5px] text-muted sm:text-[11px]">{who}</div>
        </button>
        <button onClick={toggle} title={ms.paused ? "Play" : "Pause"} aria-label={ms.paused ? "Play" : "Pause"}
          className="grid size-10 place-items-center text-fg sm:size-7 sm:text-faint sm:hover:text-fg">
          <PlayIcon className="size-5 fill-current sm:size-3.5" strokeWidth={ms.paused ? 2 : 0} />
        </button>
        <button onClick={next} disabled={!hasNext} title="Next" aria-label="Next" className={cx(btn, "size-10 text-fg sm:hidden")}>
          <SkipForward className="size-[18px] fill-current" />
        </button>
        <button onClick={onOpen} title="Open" aria-label="Open" className={cx(btn, "hidden size-7 sm:grid")}><Maximize2 className="size-3.5" /></button>
        <button onClick={onClose} title="Close" aria-label="Close" className={cx(btn, "size-10 sm:size-7")}><X className="size-5 sm:size-4" /></button>
      </div>

      {/* desktop music bar */}
      {audio && (
        <div className="hidden min-w-0 flex-1 items-center gap-6 pl-3 sm:flex">
          <button onClick={onOpen} className="w-[26%] min-w-0 text-left">
            <div className="truncate text-[13px] text-fg/90 hover:text-white">{file.title}</div>
            <div className="truncate text-[11.5px] text-muted">{who}</div>
          </button>
          <div className="mx-auto flex max-w-xl flex-1 flex-col items-center gap-1">
            <div className="flex items-center gap-5">
              <button onClick={prev} title="Previous" aria-label="Previous" className={cx(btn, "size-7")}><SkipBack className="size-4 fill-current" /></button>
              <button onClick={toggle} title={ms.paused ? "Play" : "Pause"} aria-label={ms.paused ? "Play" : "Pause"}
                className="grid size-8 place-items-center rounded-full bg-fg text-ink transition-colors hover:bg-white">
                <PlayIcon className="size-4 fill-current" strokeWidth={ms.paused ? 2 : 0} />
              </button>
              <button onClick={next} disabled={!hasNext} title="Next" aria-label="Next" className={cx(btn, "size-7")}><SkipForward className="size-4 fill-current" /></button>
            </div>
            <div className="flex w-full items-center gap-2 font-mono text-[10.5px] text-faint tabular">
              <span className="w-9 text-right">{duration(ms.time) || "0:00"}</span>
              <Seek el={el} frac={frac} dur={ms.dur} />
              <span className="w-9">{duration(ms.dur) || "0:00"}</span>
            </div>
          </div>
          <div className="flex w-[26%] justify-end gap-1">
            <button onClick={onOpen} title="Open" aria-label="Open" className={cx(btn, "size-8")}><Maximize2 className="size-4" /></button>
            <button onClick={onClose} title="Close" aria-label="Close" className={cx(btn, "size-8")}><X className="size-4" /></button>
          </div>
        </div>
      )}
    </>
  );
}

export function usePlayer() {
  const p = useContext(Ctx);
  if (!p) throw new Error("usePlayer outside provider");
  return p;
}
