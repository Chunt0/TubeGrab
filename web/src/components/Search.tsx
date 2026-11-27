import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ChevronRight, Loader2, Search as SearchIcon, X } from "lucide-react";
import { collectionCoverUrl, isActive, urls, type Collection, type LibraryFile, type Page } from "../api";
import { useStore } from "../store";
import { Link, artistPath, collectionPath, playlistPath, useRouter, watchPath } from "../router";
import { useQuery } from "../lib/cache";
import { cx, duration } from "../lib/format";
import { matches, rank, words } from "../lib/text";
import { useDebounced } from "../pages/Music";
import { KIND_LABEL } from "./CollectionCard";
import { PlaylistCover } from "./PlaylistCover";
import { Thumb } from "./Thumb";
import { GrabOptions, isUrl, useGrab } from "./GrabForm";

const typing = (el: EventTarget | null) =>
  el instanceof HTMLElement && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));

interface Hit { id: string; to: string; title: string; sub?: string; pic: ReactNode; end?: string }
interface Section { title: string; hits: Hit[] }

const square = "size-9 shrink-0 overflow-hidden rounded-sm bg-surface";
const wide = "h-9 w-16 shrink-0 overflow-hidden rounded-sm bg-surface";

function CollectionPic({ c, className }: { c: Collection; className: string }) {
  const url = collectionCoverUrl(c);
  return (
    <span className={cx("block", className)} style={!c.cover_url && c.covers[0]?.color ? { backgroundColor: c.covers[0].color } : undefined}>
      {url && <img src={url} alt="" loading="lazy" decoding="async" className="size-full object-cover" />}
    </span>
  );
}

/** Best first (starts with the query, then a word that does), at most `n`. */
function top<T>(list: T[], q: string, name: (x: T) => string, n: number) {
  return list.map((x) => [rank(q, name(x)), x] as const).sort((a, b) => a[0] - b[0]).slice(0, n).map(([, x]) => x);
}

function useSections(text: string): Section[] {
  const { library, playlists } = useStore();
  const q = text.trim();
  const term = useDebounced(q, 150);
  const songs = useQuery<Page>(term ? `${urls.files({ section: "Music", q: term })}&limit=6` : null, { keep: true });
  const videos = useQuery<Page>(term ? `${urls.files({ section: "videos", q: term })}&limit=5` : null, { keep: true });

  const local = useMemo(() => {
    const t = words(q);
    if (!t.length) return [];
    const all = library?.collections ?? [];
    const music = all.filter((c) => c.section === "Music");
    const artists = new Map<string, Collection>();
    for (const c of music) {
      const a = c.artist ?? "";
      const seen = artists.get(a);
      if (a && (!seen || (c.kind === "album" && (seen.kind !== "album" || (c.year ?? "") > (seen.year ?? ""))))) artists.set(a, c);
    }
    const out: Section[] = [];
    const add = (title: string, hits: Hit[]) => { if (hits.length) out.push({ title, hits }); };
    add("Artists", top([...artists].filter(([a]) => matches(t, a)), q, ([a]) => a, 4).map(([a, c]) => ({
      id: `a:${a}`, to: artistPath(a), title: a, pic: <CollectionPic c={c} className={square} />,
    })));
    add("Albums", top(music.filter((c) => c.kind === "album" && matches(t, c.title, c.artist, c.year)), q, (c) => c.title, 6).map((c) => ({
      id: `c:${c.key}`, to: collectionPath(c.key), title: c.title, sub: [c.artist, c.year].filter(Boolean).join(" · "),
      pic: <CollectionPic c={c} className={square} />,
    })));
    add("Channels", top(all.filter((c) => c.section !== "Music" && matches(t, c.title)), q, (c) => c.title, 4).map((c) => ({
      id: `c:${c.key}`, to: collectionPath(c.key), title: c.title, sub: `${KIND_LABEL[c.kind]} · ${c.count}`,
      pic: <CollectionPic c={c} className={wide} />,
    })));
    add("Playlists", top(playlists.filter((p) => matches(t, p.title)), q, (p) => p.title, 4).map((p) => ({
      id: `p:${p.id}`, to: playlistPath(p.id), title: p.title, sub: `${p.count} ${p.count === 1 ? "item" : "items"}`, pic: <PlaylistCover p={p} className="size-9 shrink-0" />,
    })));
    return out;
  }, [q, library, playlists]);

  const fileHits = (files: LibraryFile[] | undefined, video: boolean): Hit[] => (files ?? []).map((f) => ({
    id: `f:${f.name}`, to: watchPath(f.name), title: f.title,
    sub: video ? f.uploader ?? undefined : [f.artist, f.album].filter(Boolean).join(" · "),
    pic: <Thumb file={f} className={video ? wide : square} />, end: duration(f.duration),
  }));
  if (!q) return [];
  const live = term === q;   // (server answers for an older query would jump around)
  const out = [...local];
  const s = live ? fileHits(songs.data?.files, false) : [];
  const v = live ? fileHits(videos.data?.files, true) : [];
  if (s.length) out.splice(Math.min(2, out.length), 0, { title: "Songs", hits: s });
  if (v.length) out.push({ title: "Videos", hits: v });
  return out;
}

/** Search everything, or paste a link to download it: ⌘K or "/" anywhere, and
 *  pasting a link anywhere opens it with the link in. */
export function SearchDialog() {
  const { grabOpen, grabPrefill, openGrab, closeGrab } = useStore();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && !typing(e.target))) {
        e.preventDefault();
        openGrab();
      }
    };
    const onPaste = (e: ClipboardEvent) => {
      if (typing(e.target) || grabOpen) return;
      const t = e.clipboardData?.getData("text") ?? "";
      if (isUrl(t)) { e.preventDefault(); openGrab(t.trim()); }
    };
    addEventListener("keydown", onKey);
    addEventListener("paste", onPaste);
    return () => { removeEventListener("keydown", onKey); removeEventListener("paste", onPaste); };
  }, [openGrab, grabOpen]);

  useEffect(() => {
    if (!grabOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, [grabOpen]);

  if (!grabOpen) return null;
  return (
    <div className="fixed inset-0 z-[60] flex justify-center animate-fade sm:p-4 sm:pt-[12vh]">
      <div className="absolute inset-0 hidden bg-black/75 sm:block" onClick={closeGrab} />
      <Panel prefill={grabPrefill} close={closeGrab} />
    </div>
  );
}

function Panel({ prefill, close }: { prefill: string; close: () => void }) {
  const { navigate } = useRouter();
  const { jobs } = useStore();
  const [text, setText] = useState(prefill);
  const [sel, setSel] = useState(0);
  const g = useGrab(close);
  const list = useRef<HTMLDivElement>(null);
  const link = isUrl(text);
  const sections = useSections(link ? "" : text);
  const hits = sections.flatMap((s) => s.hits);
  const active = jobs.filter(isActive);

  useEffect(() => setSel(0), [text]);
  useEffect(() => {
    list.current?.querySelector(`[data-i="${sel}"]`)?.scrollIntoView({ block: "nearest" });
  }, [sel]);

  const open = (h: Hit | undefined) => { if (h) { close(); navigate(h.to); } };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") { e.preventDefault(); close(); }
    else if (link) { if (e.key === "Enter") { e.preventDefault(); g.submit(text); } }
    else if (e.key === "ArrowDown" && hits.length) { e.preventDefault(); setSel((i) => (i + 1) % hits.length); }
    else if (e.key === "ArrowUp" && hits.length) { e.preventDefault(); setSel((i) => (i - 1 + hits.length) % hits.length); }
    else if (e.key === "Enter") { e.preventDefault(); open(hits[sel]); }
  };

  let i = -1;
  return (
    <div role="dialog" aria-modal="true" aria-label="Search"
      className="relative flex h-dvh w-full flex-col bg-surface pt-[env(safe-area-inset-top)] sm:h-auto sm:max-h-[76vh] sm:max-w-2xl sm:rounded-sm sm:border sm:border-line-strong sm:pt-0">
      <div className={cx("flex shrink-0 items-center gap-3 border-b px-4", g.error ? "border-bad/50" : "border-line")}>
        <SearchIcon className="size-4 shrink-0 text-faint" />
        <input autoFocus value={text} onKeyDown={onKey} onChange={(e) => { setText(e.target.value); g.setError(null); }}
          placeholder="Search or paste a link" spellCheck={false} autoComplete="off" enterKeyHint={link ? "go" : "search"}
          className="h-14 min-w-0 flex-1 bg-transparent text-[15px] outline-none placeholder:text-faint" />
        <button onClick={close} aria-label="Close" className="-mr-1 grid size-8 place-items-center text-faint hover:text-fg">
          <X className="size-4" />
        </button>
      </div>

      <div ref={list} className="min-h-0 flex-1 overflow-y-auto overscroll-contain pb-[env(safe-area-inset-bottom)]">
        {link ? (
          <div className="p-4">
            <GrabOptions g={g}>
              <button onClick={() => g.submit(text)} disabled={g.busy}
                className="ml-auto flex h-8 w-20 items-center justify-center rounded-sm bg-fg text-[13px] font-medium text-ink hover:bg-white disabled:opacity-60">
                {g.busy ? <Loader2 className="size-4 animate-spin" /> : "Add"}
              </button>
            </GrabOptions>
            {g.error && <p className="mt-3 text-xs text-bad">{g.error}</p>}
          </div>
        ) : text.trim() ? (
          sections.length ? sections.map((s) => (
            <section key={s.title} className="py-2">
              <h3 className="px-4 pb-1 pt-1.5 font-mono text-[11px] text-faint">{s.title}</h3>
              {s.hits.map((h) => {
                const n = ++i;
                return (
                  <div key={h.id} data-i={n} onMouseMove={() => n !== sel && setSel(n)}>
                    <Link to={h.to} onClick={close} className={cx("flex items-center gap-3 px-4 py-1.5", n === sel && "bg-hover")}>
                      {h.pic}
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[13.5px] text-fg/90">{h.title}</span>
                        {h.sub && <span className="block truncate text-[11.5px] text-muted">{h.sub}</span>}
                      </span>
                      {h.end && <span className="shrink-0 font-mono text-[11px] text-muted tabular">{h.end}</span>}
                    </Link>
                  </div>
                );
              })}
            </section>
          )) : <p className="px-4 py-10 text-center text-[13px] text-muted">No matches.</p>
        ) : (
          <div className="px-4 py-3">
            {active.map((j) => (
              <div key={j.id} className="mb-2.5">
                <div className="flex items-baseline gap-3 text-[12.5px]">
                  <span className="min-w-0 flex-1 truncate text-fg/85">{j.title || j.url}</span>
                  <span className="shrink-0 font-mono text-[11px] text-muted tabular">
                    {j.playlist_count && j.playlist_count > 1 ? `${j.playlist_index ?? "?"}/${j.playlist_count} · ` : ""}
                    {j.status === "queued" ? "queued" : `${Math.round(j.percent ?? 0)}%`}
                  </span>
                </div>
                <div className="mt-1 h-px bg-line"><div className="h-px bg-fg transition-[width]" style={{ width: `${j.percent ?? 0}%` }} /></div>
              </div>
            ))}
            <Link to="/downloads" onClick={close} className="flex items-center justify-between py-1 text-[12.5px] text-muted hover:text-fg">
              Downloads <ChevronRight className="size-3.5" />
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}
