import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import {
  Captions, ChevronsLeft, ChevronsRight, Loader2, Maximize, Minimize, Pause, PictureInPicture2, Play,
  SkipBack, SkipForward, Volume1, Volume2, VolumeX,
} from "lucide-react";
import { usePlayer } from "../player";
import { cx, duration } from "../lib/format";
import { chapterAt, segmentAt, useTranscript } from "../lib/transcript";
import { useMediaState } from "../lib/media";

const SPEEDS = [0.75, 1, 1.25, 1.5, 1.75, 2];
const CC_KEY = "tubegrab.cc";
const speedLabel = (v: number) => `${v}×`;

const typing = (el: EventTarget | null) =>
  el instanceof HTMLElement && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
const fmt = (s: number) => duration(s) || "0:00";

function Ctl({ label, onClick, className, children, disabled }:
  { label: string; onClick: () => void; className?: string; children: ReactNode; disabled?: boolean }) {
  return (
    <button
      type="button" aria-label={label} title={label} disabled={disabled}
      // mouse/touch clicks drop focus so Space keeps meaning play/pause
      onClick={(e) => { onClick(); if (e.detail) e.currentTarget.blur(); }}
      className={cx("pointer-events-auto grid place-items-center text-white/90 transition-colors hover:text-white disabled:opacity-30", className)}
    >
      {children}
    </button>
  );
}

type Streak = { dir: -1 | 1; secs: number; n: number };

/**
 * The Watch page's player: hosts the shared media element plus YouTube-style
 * controls — double-tap sides to skip ±10s (taps stack), tap to show controls,
 * prev/play/next, scrubber, fullscreen, PiP, keyboard shortcuts.
 */
export function PlayerStage({ music }: { music: boolean }) {
  const player = usePlayer();
  const { media: el, file, next, prev, speed, setSpeed } = player;
  const chapters = file?.chapters ?? null;
  const segs = useTranscript(file);
  const [cc, setCcState] = useState(() => { try { return localStorage.getItem(CC_KEY) === "1"; } catch { return false; } });
  const setCc = useCallback((v: boolean) => { setCcState(v); try { localStorage.setItem(CC_KEY, v ? "1" : "0"); } catch { /* ok */ } }, []);
  const [speedOpen, setSpeedOpen] = useState(false);
  const ms = useMediaState(el);
  const stage = useRef<HTMLDivElement>(null);
  const slot = useRef<HTMLDivElement>(null);
  const bar = useRef<HTMLDivElement>(null);

  const [active, setActive] = useState(true);   // recently interacted → controls visible
  const [drag, setDrag] = useState<number | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const [streak, setStreak] = useState<Streak | null>(null);
  const [flash, setFlash] = useState<{ icon: ReactNode; text?: string; k: number } | null>(null);
  const [fs, setFs] = useState(false);
  const hideT = useRef(0), streakT = useRef(0), flashT = useRef(0);
  const lastTap = useRef({ t: 0, x: 0, mouse: false });

  const { hasNext } = player;
  const shown = active || ms.paused || drag != null || speedOpen;
  const canPip = file?.kind === "video" && document.pictureInPictureEnabled;

  // borrow the app-wide media element while mounted (layout effect: runs during
  // the navigation click, so the browser still allows PiP on leave)
  useLayoutEffect(() => {
    const s = slot.current!;
    player.attach(s);
    return () => player.detach(s);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const poke = useCallback(() => {
    setActive(true);
    clearTimeout(hideT.current);
    hideT.current = window.setTimeout(() => setActive(false), 2600);
  }, []);
  useEffect(() => () => { clearTimeout(hideT.current); clearTimeout(streakT.current); clearTimeout(flashT.current); }, []);

  const showFlash = useCallback((icon: ReactNode, text?: string) => {
    setFlash({ icon, text, k: Date.now() });
    clearTimeout(flashT.current);
    flashT.current = window.setTimeout(() => setFlash(null), 550);
  }, []);

  const seekBy = useCallback((d: number) => {
    el.currentTime = Math.min(Math.max(0, el.currentTime + d), el.duration || Infinity);
  }, [el]);
  const toggle = useCallback(() => { if (el.paused) el.play().catch(() => {}); else el.pause(); }, [el]);

  const skip = useCallback((dir: -1 | 1) => {
    seekBy(dir * 10);
    setStreak((s) => (s && s.dir === dir ? { dir, secs: s.secs + 10, n: s.n + 1 } : { dir, secs: 10, n: 1 }));
    clearTimeout(streakT.current);
    streakT.current = window.setTimeout(() => setStreak(null), 750);
  }, [seekBy]);

  const toggleFs = useCallback(() => {
    const st = stage.current;
    if (document.fullscreenElement) { document.exitFullscreen().catch(() => {}); return; }
    const native = () => (el as HTMLVideoElement & { webkitEnterFullscreen?: () => void }).webkitEnterFullscreen?.();
    if (!st?.requestFullscreen || !document.fullscreenEnabled) { native(); return; }   // iPhone
    st.requestFullscreen().then(() => {
      if (!music) (screen.orientation as ScreenOrientation & { lock?: (o: string) => Promise<void> }).lock?.("landscape").catch(() => {});
    }).catch(native);
  }, [el, music]);
  useEffect(() => {
    const on = () => setFs(document.fullscreenElement === stage.current);
    document.addEventListener("fullscreenchange", on);
    return () => document.removeEventListener("fullscreenchange", on);
  }, []);

  // taps / clicks on the picture
  const onUp = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const now = performance.now();
    const mouse = e.pointerType === "mouse";
    const last = lastTap.current;
    const dbl = now - last.t < 300 && last.mouse === mouse;
    lastTap.current = { t: now, x, mouse };
    if (mouse) {
      if (dbl) toggleFs();
      else { toggle(); showFlash(el.paused ? <Play className="size-7 fill-current" /> : <Pause className="size-7 fill-current" strokeWidth={0} />); }
      return;
    }
    const zone = x < 0.35 ? -1 : x > 0.65 ? 1 : 0;
    // keep stacking skips while a streak is going (like YouTube), even on single taps
    if (zone && (dbl || streak?.dir === zone)) { setActive(false); skip(zone as -1 | 1); return; }
    if (shown && !ms.paused) { setActive(false); clearTimeout(hideT.current); }
    else poke();
  };

  // scrubber
  const fracAt = (clientX: number) => {
    const r = bar.current!.getBoundingClientRect();
    return Math.min(1, Math.max(0, (clientX - r.left) / r.width));
  };
  const onBarDown = (e: React.PointerEvent) => {
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    setDrag(fracAt(e.clientX));
  };
  const onBarMove = (e: React.PointerEvent) => {
    if (drag != null) setDrag(fracAt(e.clientX));
    else if (e.pointerType === "mouse") setHover(fracAt(e.clientX));
  };
  const onBarUp = (e: React.PointerEvent) => {
    e.stopPropagation();
    if (drag == null) return;
    if (ms.dur) el.currentTime = fracAt(e.clientX) * ms.dur;
    setDrag(null);
    poke();
  };

  // keyboard (YouTube's keys)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || typing(e.target) || document.querySelector("[role=dialog]")) return;
      const k = e.key;
      const act = (fn: () => void) => { e.preventDefault(); fn(); poke(); };
      if (k === " " || k === "k") act(() => { toggle(); showFlash(el.paused ? <Play className="size-7 fill-current" /> : <Pause className="size-7 fill-current" strokeWidth={0} />); });
      else if (k === "j") act(() => skip(-1));
      else if (k === "l") act(() => skip(1));
      else if (k === "ArrowLeft") act(() => { seekBy(-5); showFlash(<ChevronsLeft className="size-7" />, "5s"); });
      else if (k === "ArrowRight") act(() => { seekBy(5); showFlash(<ChevronsRight className="size-7" />, "5s"); });
      else if (k === "ArrowUp" || k === "ArrowDown") act(() => {
        el.muted = false;
        el.volume = Math.min(1, Math.max(0, el.volume + (k === "ArrowUp" ? 0.05 : -0.05)));
        showFlash(<Volume2 className="size-7" />, `${Math.round(el.volume * 100)}%`);
      });
      else if (k === "m") act(() => { el.muted = !el.muted; showFlash(el.muted ? <VolumeX className="size-7" /> : <Volume2 className="size-7" />); });
      else if (k === "f") act(toggleFs);
      else if (k === "N") act(next);
      else if ((k === ">" || k === "<") && !music) act(() => {
        const i = SPEEDS.indexOf(el.playbackRate);
        const v = SPEEDS[Math.min(SPEEDS.length - 1, Math.max(0, (i < 0 ? 1 : i) + (k === ">" ? 1 : -1)))];
        setSpeed(v);
        showFlash(<span className="font-mono text-base">{speedLabel(v)}</span>);
      });
      else if (k === "c" && segs?.length) act(() => setCc(!cc));
      else if (k === "P") act(prev);
      else if (/^[0-9]$/.test(k) && el.duration) act(() => { el.currentTime = (Number(k) / 10) * el.duration; });
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, [el, toggle, skip, seekBy, toggleFs, next, prev, poke, showFlash, music, setSpeed, segs, cc, setCc]);

  const pos = drag ?? (ms.dur ? ms.time / ms.dur : 0);
  const peek = drag ?? hover;
  const chapterNow = chapters ? chapterAt(chapters, drag != null ? drag * ms.dur : ms.time) : -1;
  const line = cc && segs ? segs[segmentAt(segs, ms.time)] : undefined;
  const VolIcon = ms.muted || ms.volume === 0 ? VolumeX : ms.volume < 0.5 ? Volume1 : Volume2;
  const PlayIcon = ms.paused ? Play : Pause;

  return (
    <div
      ref={stage}
      onPointerMove={(e) => e.pointerType === "mouse" && poke()}
      onPointerLeave={(e) => { if (e.pointerType === "mouse" && !ms.paused) setActive(false); }}
      className={cx("relative -mx-4 select-none overflow-hidden bg-black sm:mx-0", fs ? "flex items-center" : "sm:rounded-sm",
        !shown && "cursor-none")}
    >
      <div ref={slot} className={cx("w-full", fs ? "h-full" : music ? "aspect-square max-h-[62dvh] sm:aspect-video sm:max-h-[78vh]" : "aspect-video sm:max-h-[78vh]")} />

      {/* gesture layer */}
      <div className="absolute inset-0 touch-manipulation" onPointerUp={onUp} />

      {/* double-tap skip ripple */}
      {streak && (
        <div key={streak.dir} className={cx("pointer-events-none absolute inset-y-0 flex w-[42%] flex-col items-center justify-center gap-1 bg-white/15 text-white animate-fade [filter:drop-shadow(0_1px_2px_rgb(0_0_0/.7))]",
          streak.dir < 0 ? "left-0 rounded-r-[50%]" : "right-0 rounded-l-[50%]")}>
          {streak.dir < 0 ? <ChevronsLeft className="size-7" /> : <ChevronsRight className="size-7" />}
          <span className="text-xs font-medium tabular">{streak.secs} seconds</span>
        </div>
      )}

      {/* centre flash (clicks / keys) */}
      {flash && (
        <div key={flash.k} className="pointer-events-none absolute left-1/2 top-1/2 flex size-16 -translate-x-1/2 -translate-y-1/2 flex-col items-center justify-center rounded-sm bg-black/55 text-white animate-[fade_.15s_ease-out_both]">
          {flash.icon}
          {flash.text && <span className="mt-0.5 font-mono text-[10px] tabular">{flash.text}</span>}
        </div>
      )}

      {/* captions (drawn here so they sit above the controls and work in full screen) */}
      {line && (
        <div className={cx("pointer-events-none absolute inset-x-0 flex justify-center px-6 transition-[bottom] duration-200", shown ? "bottom-[4.5rem]" : "bottom-5")}>
          <span className={cx("max-w-[52rem] bg-black/75 px-2 py-0.5 text-center leading-snug text-white [box-decoration-break:clone]",
            fs ? "text-2xl" : "text-[13px] sm:text-[17px]")}>{line.text}</span>
        </div>
      )}

      {ms.waiting && !ms.paused && (
        <Loader2 className="pointer-events-none absolute left-1/2 top-1/2 size-9 -translate-x-1/2 -translate-y-1/2 animate-spin text-white/80" />
      )}

      {/* controls */}
      <div className={cx("pointer-events-none absolute inset-0 transition-opacity duration-200", shown ? "opacity-100" : "opacity-0 [&_*]:!pointer-events-none")}>
        <div className="absolute inset-x-0 bottom-0 h-28 bg-gradient-to-t from-black/75 to-transparent" />

        {/* touch: big centre buttons */}
        {!(ms.waiting && !ms.paused) && (
          <div className="absolute inset-0 hidden items-center justify-center gap-10 [@media(hover:none)]:flex">
            <Ctl label="Previous" onClick={() => { prev(); poke(); }} className="size-12 rounded-full bg-black/35">
              <SkipBack className="size-6 fill-current" />
            </Ctl>
            <Ctl label={ms.paused ? "Play" : "Pause"} onClick={() => { toggle(); poke(); }} className="size-16 rounded-full bg-black/35">
              <PlayIcon className="size-8 fill-current" strokeWidth={ms.paused ? 2 : 0} />
            </Ctl>
            <Ctl label="Next" onClick={() => { next(); poke(); }} disabled={!hasNext} className="size-12 rounded-full bg-black/35">
              <SkipForward className="size-6 fill-current" />
            </Ctl>
          </div>
        )}

        <div className="absolute inset-x-0 bottom-0 px-3 pb-1.5 sm:px-4">
          {/* scrubber */}
          <div
            ref={bar}
            onPointerDown={onBarDown} onPointerMove={onBarMove} onPointerUp={onBarUp}
            onPointerLeave={() => setHover(null)} onPointerCancel={() => setDrag(null)}
            className="group/bar pointer-events-auto relative flex h-6 cursor-pointer touch-none items-center"
          >
            <div className={cx("relative w-full bg-white/25 transition-[height]", drag != null ? "h-[5px]" : "h-[3px] group-hover/bar:h-[5px]")}>
              {hover != null && <div className="absolute inset-y-0 left-0 bg-white/25" style={{ width: `${hover * 100}%` }} />}
              <div className="absolute inset-y-0 left-0 bg-white" style={{ width: `${pos * 100}%` }} />
              {ms.dur > 0 && chapters?.slice(1).map((c) => (
                <div key={c.start} className="absolute inset-y-0 w-[2px] -translate-x-1/2 bg-black/80" style={{ left: `${(c.start / ms.dur) * 100}%` }} />
              ))}
            </div>
            <div className={cx("absolute h-3.5 w-[3px] -translate-x-1/2 bg-white transition-opacity", drag != null ? "opacity-100" : "opacity-0 group-hover/bar:opacity-100 [@media(hover:none)]:opacity-100")}
              style={{ left: `${pos * 100}%` }} />
            {peek != null && ms.dur > 0 && (
              <div className="absolute bottom-6 flex max-w-60 -translate-x-1/2 flex-col items-center bg-black/80 px-1.5 py-0.5 text-white"
                style={{ left: `clamp(4rem, ${peek * 100}%, calc(100% - 4rem))` }}>
                {chapters && chapterAt(chapters, peek * ms.dur) >= 0 && (
                  <span className="max-w-full truncate text-[11px]">{chapters[chapterAt(chapters, peek * ms.dur)].title}</span>
                )}
                <span className="font-mono text-[11px] tabular">{fmt(peek * ms.dur)}</span>
              </div>
            )}
          </div>

          <div className="flex h-8 items-center gap-1 text-white">
            {/* pointer devices: transport + volume in the bar */}
            <div className="hidden items-center gap-1 [@media(hover:hover)]:flex">
              <Ctl label="Previous (Shift+P)" onClick={prev} className="size-8"><SkipBack className="size-4 fill-current" /></Ctl>
              <Ctl label={ms.paused ? "Play (k)" : "Pause (k)"} onClick={toggle} className="size-8">
                <PlayIcon className="size-[18px] fill-current" strokeWidth={ms.paused ? 2 : 0} />
              </Ctl>
              <Ctl label="Next (Shift+N)" onClick={next} disabled={!hasNext} className="size-8"><SkipForward className="size-4 fill-current" /></Ctl>
              <div className="group/vol flex items-center">
                <Ctl label={ms.muted ? "Unmute (m)" : "Mute (m)"} onClick={() => { el.muted = !el.muted; }} className="size-8">
                  <VolIcon className="size-[18px]" />
                </Ctl>
                <input
                  type="range" min={0} max={1} step={0.02} aria-label="Volume"
                  value={ms.muted ? 0 : ms.volume}
                  onChange={(e) => { el.volume = Number(e.target.value); el.muted = el.volume === 0; }}
                  className="pointer-events-auto h-1 w-0 cursor-pointer opacity-0 accent-white transition-all group-hover/vol:w-20 group-hover/vol:opacity-100 focus:w-20 focus:opacity-100"
                />
              </div>
            </div>
            <span className="ml-1 shrink-0 font-mono text-[11.5px] text-white/90 tabular">
              {fmt(drag != null ? drag * ms.dur : ms.time)} <span className="text-white/50">/ {fmt(ms.dur)}</span>
            </span>
            {chapters && chapterNow >= 0 && (
              <span className="ml-2 min-w-0 truncate text-[12px] text-white/75">· {chapters[chapterNow].title}</span>
            )}
            <div className="flex-1" />
            {!music && (
              <div className="pointer-events-auto relative">
                <Ctl label="Playback speed (< >)" onClick={() => setSpeedOpen((o) => !o)} className="h-8 min-w-10 px-1.5">
                  <span className={cx("font-mono text-[12px] tabular", speed !== 1 && "text-white")}>{speedLabel(speed)}</span>
                </Ctl>
                {speedOpen && (
                  <div role="menu" className="absolute bottom-9 right-0 z-10 w-24 rounded-sm border border-white/15 bg-black/90 py-1">
                    {SPEEDS.map((v) => (
                      <button key={v} role="menuitemradio" aria-checked={v === speed} type="button"
                        onClick={() => { setSpeed(v); setSpeedOpen(false); poke(); }}
                        className={cx("flex w-full items-center justify-between px-3 py-1.5 font-mono text-[12px] tabular hover:bg-white/10",
                          v === speed ? "text-white" : "text-white/60")}>
                        {speedLabel(v)}{v === 1 && <span className="font-sans text-[11px] text-white/40">normal</span>}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
            {!!segs?.length && (
              <Ctl label={cc ? "Captions off (c)" : "Captions (c)"} onClick={() => setCc(!cc)} className="relative size-8">
                <Captions className={cx("size-[18px]", !cc && "opacity-60")} />
                {cc && <span className="absolute inset-x-2 bottom-1 h-[2px] bg-white" />}
              </Ctl>
            )}
            {canPip && (
              <Ctl label="Picture in picture" onClick={() => el.requestPictureInPicture().catch(() => {})} className="size-8">
                <PictureInPicture2 className="size-[18px]" />
              </Ctl>
            )}
            <Ctl label={fs ? "Exit full screen (f)" : "Full screen (f)"} onClick={toggleFs} className="size-8">
              {fs ? <Minimize className="size-[18px]" /> : <Maximize className="size-[18px]" />}
            </Ctl>
          </div>
        </div>
      </div>
    </div>
  );
}
