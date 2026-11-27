import { useEffect, useMemo, useState } from "react";
import { Heart as HeartIcon, Search, X } from "lucide-react";
import { useStore } from "../store";
import { collectionCoverUrl, urls, type Collection, type LibraryFile } from "../api";
import { Link, artistPath, useParam, useRouter, watchPath } from "../router";
import { bytes, cx, duration, longDuration } from "../lib/format";
import { AlbumCard } from "../components/CollectionCard";
import { GrabForm } from "../components/GrabForm";
import { Thumb } from "../components/Thumb";
import { ItemMenu } from "../components/ItemMenu";
import { Tab } from "../components/ui";
import { Heart } from "../components/Heart";
import { Grouped, type Group } from "../components/Grouped";
import { usePaged } from "../lib/cache";
import { byName, decade, letter, matches, words } from "../lib/text";

export type View = "albums" | "artists" | "songs";
const VIEWS: { id: View; label: string }[] = [
  { id: "albums", label: "Albums" },
  { id: "artists", label: "Artists" },
  { id: "songs", label: "Songs" },
];
const SORTS: Record<View, { id: string; label: string }[]> = {
  albums: [
    { id: "artist", label: "Artist" },
    { id: "title", label: "Title" },
    { id: "year", label: "Year" },
  ],
  artists: [{ id: "name", label: "Name" }],
  songs: [
    { id: "title", label: "Title" },
    { id: "artist", label: "Artist" },
  ],
};

export const ALBUM_GRID = "grid grid-cols-2 gap-x-4 gap-y-7 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-7";

export function SectionHead({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="mb-4 flex items-baseline justify-between gap-4 border-b border-line pb-2">
      <h2 className="text-[13px] font-medium text-fg">{title}</h2>
      {children}
    </div>
  );
}

export function SearchBox({ q, setQ, placeholder = "Search", bare }:
  { q: string; setQ: (v: string) => void; placeholder?: string; bare?: boolean }) {
  return (
    <div className="relative w-full sm:w-56">
      <Search className="pointer-events-none absolute left-0 top-1/2 size-3.5 -translate-y-1/2 text-faint" />
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={placeholder}
        className={`h-8 w-full bg-transparent pl-6 pr-6 text-[13px] outline-none transition-colors placeholder:text-faint ${bare ? "" : "border-b border-line focus:border-fg/50"}`} />
      {q && (
        <button onClick={() => setQ("")} className="absolute right-0 top-1/2 -translate-y-1/2 p-1 text-faint hover:text-fg" aria-label="Clear search">
          <X className="size-3.5" />
        </button>
      )}
    </div>
  );
}

/** Compact track row used in search results and artist pages. */
export function TrackRow({ f, n, showAlbum = true, list }: { f: LibraryFile; n?: number | null; showAlbum?: boolean; list?: string | null }) {
  return (
    <div className="group flex items-center border-b border-line">
    <Link to={watchPath(f.name, list)} className="flex min-w-0 flex-1 items-center gap-3 py-1.5">
      {n !== undefined
        ? <span className="w-6 shrink-0 text-right font-mono text-xs text-faint tabular">{n ?? "·"}</span>
        : <Thumb file={f} className="size-9 shrink-0 rounded-sm" />}
      <div className="min-w-0 flex-1">
        <div className="truncate text-[13px] text-fg/90 group-hover:text-white">{f.title}</div>
        {showAlbum && <div className="truncate text-[11.5px] text-muted">{[f.artist, f.album].filter(Boolean).join(" · ")}</div>}
      </div>
      <span className="font-mono text-[11px] text-muted tabular">{duration(f.duration)}</span>
    </Link>
    {f.section === "Music" && <Heart kind="song" id={f.name} file={f} quiet className="ml-1" />}
    <ItemMenu file={f} />
    </div>
  );
}

/** `value`, once it has stopped changing for `ms`. */
export function useDebounced<T>(value: T, ms = 220): T {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}

/** Last-used tab and sorts, kept on the device. */
const remember = {
  get: (k: string) => { try { return localStorage.getItem(`music.${k}`); } catch { return null; } },
  set: (k: string, v: string) => { try { localStorage.setItem(`music.${k}`, v); } catch { /* ok */ } },
};

interface Artist { name: string; albums: number; tracks: number; cover: Collection }

/** Same-letter (or same-decade) runs of an already sorted list. */
function groupBy<T>(list: T[], key: (x: T) => string, short?: (k: string) => string): Group<T>[] {
  const out: Group<T>[] = [];
  for (const x of list) {
    const k = key(x);
    if (out.at(-1)?.id !== k) out.push({ id: k, label: k, short: short?.(k), items: [] });
    out.at(-1)!.items.push(x);
  }
  return out;
}

function albumGroups(list: Collection[], sort: string): Group<Collection>[] {
  const name = (c: Collection) => (c.kind === "singles" ? "Singles" : c.title);
  const yearDesc = (a: Collection, b: Collection) => (b.year ?? "").localeCompare(a.year ?? "");
  switch (sort) {
    case "title":
      return groupBy([...list].sort((a, b) => byName(name(a), name(b))), (c) => letter(name(c)));
    case "year":
      return groupBy([...list].sort((a, b) => yearDesc(a, b) || byName(a.artist, b.artist)), (c) => decade(c.year),
        (k) => (k.endsWith("0s") ? `’${k.slice(2, 4)}` : "?"));
    default:   // artist
      return groupBy([...list].sort((a, b) => byName(a.artist, b.artist) || yearDesc(a, b)), (c) => letter(c.artist));
  }
}

const ARTIST_GRID = "grid grid-cols-1 gap-x-8 gap-y-0 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4";

function ArtistRow({ a }: { a: Artist }) {
  const cover = collectionCoverUrl(a.cover);
  return (
    <Link to={artistPath(a.name)} className="group flex items-center gap-3 border-b border-line py-2">
      <span className="size-10 shrink-0 overflow-hidden rounded-sm bg-surface"
        style={!a.cover.cover_url && a.cover.covers[0]?.color ? { backgroundColor: a.cover.covers[0].color } : undefined}>
        {cover && <img src={cover} alt="" loading="lazy" decoding="async" className="size-full object-cover" />}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13px] text-fg/90 group-hover:text-white">{a.name}</span>
        <span className="block font-mono text-[11px] text-faint tabular">
          {a.albums ? `${a.albums} ${a.albums === 1 ? "album" : "albums"} · ` : ""}{a.tracks} {a.tracks === 1 ? "track" : "tracks"}
        </span>
      </span>
    </Link>
  );
}

/** Only shown when there's nothing (or nothing matching) to show. */
const NoMatches = ({ fav }: { fav?: boolean }) =>
  <p className="py-16 text-center text-[13px] text-muted">{fav ? "No favorites." : "No matches."}</p>;

function Albums({ albums, q, sort, fav }: { albums: Collection[]; q: string; sort: string; fav: boolean }) {
  const { favorites } = useStore();
  const groups = useMemo(() => {
    const t = words(q);
    const hearted = new Set(favorites.albums);
    return albumGroups(albums.filter((c) => (!fav || hearted.has(c.key)) && matches(t, c.title, c.artist, c.year)), sort);
  }, [albums, q, sort, fav, favorites.albums]);
  if (!groups.length) return <NoMatches fav={fav && !q} />;
  return <Grouped groups={groups} className={ALBUM_GRID}>{(c) => <AlbumCard key={c.key} c={c} />}</Grouped>;
}

function Artists({ albums, q, fav }: { albums: Collection[]; q: string; fav: boolean }) {
  const artists = useMemo(() => {
    const m = new Map<string, Artist>();
    for (const c of albums) {
      const name = c.artist ?? "Unknown";
      const a = m.get(name) ?? { name, albums: 0, tracks: 0, cover: c };
      if (c.kind === "album") {
        // newest album's cover stands for the artist (singles only when there's nothing else)
        if (a.cover.kind !== "album" || (c.year ?? "") > (a.cover.year ?? "")) a.cover = c;
        a.albums++;
      }
      a.tracks += c.count;
      m.set(name, a);
    }
    return [...m.values()];
  }, [albums]);
  const { favorites } = useStore();
  const groups = useMemo(() => {
    const t = words(q);
    const hearted = new Set(favorites.artists);
    const list = artists.filter((a) => (!fav || hearted.has(a.name)) && matches(t, a.name));
    return groupBy(list.sort((a, b) => byName(a.name, b.name)), (a) => letter(a.name));
  }, [artists, q, fav, favorites.artists]);
  if (!groups.length) return <NoMatches fav={fav && !q} />;
  return (
    <Grouped groups={groups} className={`${ARTIST_GRID} [&>h3]:mb-0 [&>h3]:mt-6 [&>h3:first-child]:mt-0`}>
      {(a) => <ArtistRow key={a.name} a={a} />}
    </Grouped>
  );
}

function Songs({ q, sort, fav }: { q: string; sort: string; fav: boolean }) {
  const term = useDebounced(q.trim());
  const list = usePaged<LibraryFile>(urls.files({ section: "Music", q: term, sort, fav: fav ? 1 : undefined }), 80);
  if (list.loaded && !list.items.length) return <NoMatches fav={fav && !q} />;
  return (
    <>
      <p className="mb-2 font-mono text-[11px] text-faint tabular">{list.loaded ? `${list.total} ${list.total === 1 ? "song" : "songs"}` : "\u00a0"}</p>
      <div className="max-w-3xl">{list.items.map((f) => <TrackRow key={f.name} f={f} />)}</div>
      {list.sentinel}
    </>
  );
}

export function Music({ view: asked }: { view?: View }) {
  const { library } = useStore();
  const { navigate, search } = useRouter();
  const [q, setQ] = useParam("q");
  const [sortParam, setSort] = useParam("sort");
  // the Favorites switch: in the URL (back / forward), else as you last left it
  const [favParam, setFavParam] = useParam("fav");
  const [favSaved, setFavSaved] = useState(() => remember.get("fav") === "1");
  const fav = favParam ? favParam === "1" : favSaved;
  const toggleFav = () => {
    remember.set("fav", fav ? "0" : "1");
    setFavSaved(!fav);
    setFavParam(fav ? null : "1");
  };
  const stored = remember.get("view") as View | null;
  const view: View = asked ?? (VIEWS.some((v) => v.id === stored) ? stored! : "albums");

  // /music → the tab you used last
  useEffect(() => { if (!asked) navigate(`/music/${view}${search}`, true); }, [asked, view, search, navigate]);
  useEffect(() => { if (asked) remember.set("view", asked); }, [asked]);

  const sorts = SORTS[view];
  const savedSort = remember.get(`sort.${view}`);
  const sort = [sortParam, savedSort].find((s) => sorts.some((x) => x.id === s)) ?? sorts[0].id;
  const changeSort = (s: string) => { remember.set(`sort.${view}`, s); setSort(s === sorts[0].id ? null : s); };
  const go = (v: View) => {   // the filter and favorites carry over; the sort is per tab
    const p = new URLSearchParams();
    if (q) p.set("q", q);
    if (fav) p.set("fav", "1");
    const s = p.toString();
    navigate(`/music/${v}${s ? `?${s}` : ""}`, true);
  };

  const albums = useMemo(() => (library?.collections ?? []).filter((c) => c.section === "Music"), [library]);
  const artistCount = useMemo(() => new Set(albums.map((c) => c.artist ?? "")).size, [albums]);
  const stats = library?.stats.music;

  if (!library || (!library.ready && !albums.length)) {
    return <div className={`mt-10 ${ALBUM_GRID}`}>{Array.from({ length: 12 }, (_, i) => <div key={i} className="aspect-square rounded-sm bg-surface" />)}</div>;
  }
  if (!stats?.count) {
    return (
      <section className="mx-auto mt-[16vh] max-w-xl">
        <h1 className="text-[15px] font-medium">No music yet</h1>
        <div className="mt-6"><GrabForm /></div>
      </section>
    );
  }

  return (
    <>
      <div className="flex items-baseline gap-3 pt-6">
        <h1 className="text-[15px] font-medium">Music</h1>
        <span className="truncate font-mono text-[11px] text-faint tabular">
          {artistCount} artists · {stats.count} tracks · {longDuration(stats.duration)} · {bytes(stats.size)}
        </span>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-3 border-b border-line">
        <div role="tablist" className="flex items-center gap-5">
          {VIEWS.map((v) => (
            <Tab key={v.id} role="tab" aria-selected={view === v.id} active={view === v.id} onClick={() => go(v.id)}>{v.label}</Tab>
          ))}
        </div>
        <button type="button" onClick={toggleFav} aria-pressed={fav}
          aria-label="Favorites" title="Favorites"
          className={cx("ml-auto flex h-8 items-center gap-1.5 text-[13px] transition-colors", fav ? "text-fg" : "text-muted hover:text-fg")}>
          <HeartIcon className="size-3.5" fill={fav ? "currentColor" : "none"} /> Favorites
        </button>
        {sorts.length > 1 && <select value={sort} onChange={(e) => changeSort(e.target.value)} aria-label="Sort"
          className="h-8 cursor-pointer bg-transparent text-[13px] text-muted outline-none [field-sizing:content] hover:text-fg">
          {sorts.map((s) => <option key={s.id} value={s.id} className="bg-surface text-fg">{s.label}</option>)}
        </select>}
        <div className="mb-2 w-full sm:mb-0 sm:w-auto"><SearchBox q={q ?? ""} setQ={(v) => setQ(v || null)} placeholder="Filter" bare /></div>
      </div>

      <section className="mt-6">
        {view === "albums" && <Albums albums={albums} q={q ?? ""} sort={sort} fav={fav} />}
        {view === "artists" && <Artists albums={albums} q={q ?? ""} fav={fav} />}
        {view === "songs" && <Songs q={q ?? ""} sort={sort} fav={fav} />}
      </section>
    </>
  );
}
