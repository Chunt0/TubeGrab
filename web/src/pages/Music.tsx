import { useEffect, useMemo, useState } from "react";
import { Search, X } from "lucide-react";
import { useStore } from "../store";
import { urls, type Collection, type LibraryFile, type Page } from "../api";
import { Link, artistPath, watchPath } from "../router";
import { bytes, duration, longDuration } from "../lib/format";
import { AlbumCard } from "../components/CollectionCard";
import { GrabForm } from "../components/GrabForm";
import { Thumb } from "../components/Thumb";
import { ItemMenu } from "../components/ItemMenu";
import { useQuery } from "../lib/cache";
import { Grow } from "../lib/grow";

type Sort = "recent" | "artist" | "title" | "year";
const SORTS: { id: Sort; label: string }[] = [
  { id: "recent", label: "Recently added" },
  { id: "artist", label: "Artist" },
  { id: "title", label: "Title" },
  { id: "year", label: "Year" },
];
export const ALBUM_GRID = "grid grid-cols-2 gap-x-4 gap-y-7 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-7";

export function SectionHead({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="mb-4 flex items-baseline justify-between gap-4 border-b border-line pb-2">
      <h2 className="text-[13px] font-medium text-fg">{title}</h2>
      {children}
    </div>
  );
}

export function SearchBox({ q, setQ, placeholder = "Search" }: { q: string; setQ: (v: string) => void; placeholder?: string }) {
  return (
    <div className="relative w-full sm:w-56">
      <Search className="pointer-events-none absolute left-0 top-1/2 size-3.5 -translate-y-1/2 text-faint" />
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={placeholder}
        className="h-8 w-full border-b border-line bg-transparent pl-6 pr-6 text-[13px] outline-none transition-colors placeholder:text-faint focus:border-fg/50" />
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
    <ItemMenu file={f} className="ml-1" />
    </div>
  );
}

function sortAlbums(list: Collection[], sort: Sort) {
  const out = [...list];
  const cmp: Record<Sort, (a: Collection, b: Collection) => number> = {
    recent: (a, b) => b.updated - a.updated,
    artist: (a, b) => (a.artist ?? "").localeCompare(b.artist ?? "") || (b.year ?? "").localeCompare(a.year ?? ""),
    title: (a, b) => a.title.localeCompare(b.title),
    year: (a, b) => (b.year ?? "").localeCompare(a.year ?? ""),
  };
  return out.sort(cmp[sort]);
}

/** `value`, once it has stopped changing for `ms`. */
export function useDebounced<T>(value: T, ms = 220): T {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}

export function Music() {
  const { library } = useStore();
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<Sort>("recent");

  const albums = useMemo(() => (library?.collections ?? []).filter((c) => c.section === "Music"), [library]);
  const artists = useMemo(() => {
    const m = new Map<string, { name: string; albums: number; tracks: number }>();
    for (const c of albums) {
      const a = m.get(c.artist ?? "") ?? { name: c.artist ?? "Unknown", albums: 0, tracks: 0 };
      if (c.kind === "album") a.albums++;
      a.tracks += c.count;
      m.set(c.artist ?? "", a);
    }
    return [...m.values()].sort((a, b) => a.name.localeCompare(b.name));
  }, [albums]);
  const stats = library?.stats.music;

  const needle = q.trim().toLowerCase();
  const term = useDebounced(needle);
  const match = (s: string | null | undefined) => !!s && s.toLowerCase().includes(needle);
  const shownAlbums = useMemo(() => sortAlbums(needle ? albums.filter((c) => match(c.title) || match(c.artist)) : albums, sort),
    [albums, needle, sort]); // eslint-disable-line react-hooks/exhaustive-deps
  const tracks = useQuery<Page>(term ? `${urls.files({ section: "Music", q: term, sort: "title" })}&limit=50` : null, { keep: true });
  const shownTracks = needle ? tracks.data?.files ?? [] : [];

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
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3 pt-6">
        <div className="flex w-full items-baseline gap-3 sm:mr-auto sm:w-auto">
          <h1 className="text-[15px] font-medium">Music</h1>
          <span className="font-mono text-[11px] text-faint tabular">
            {artists.length} artists · {stats.count} tracks · {longDuration(stats.duration)} · {bytes(stats.size)}
          </span>
        </div>
        <select value={sort} onChange={(e) => setSort(e.target.value as Sort)} aria-label="Sort"
          className="order-2 ml-auto h-8 cursor-pointer bg-transparent text-[13px] text-muted outline-none hover:text-fg sm:order-none sm:ml-0">
          {SORTS.map((s) => <option key={s.id} value={s.id} className="bg-surface text-fg">{s.label}</option>)}
        </select>
        <div className="order-1 w-full sm:order-none sm:w-auto"><SearchBox q={q} setQ={setQ} placeholder="Artists, albums, tracks" /></div>
      </div>

      {shownTracks.length > 0 && (
        <section className="mt-8">
          <SectionHead title={`Tracks · ${tracks.data && tracks.data.total > shownTracks.length ? `${shownTracks.length} of ${tracks.data.total}` : shownTracks.length}`} />
          <div className="max-w-3xl">{shownTracks.map((f) => <TrackRow key={f.name} f={f} />)}</div>
        </section>
      )}

      <section className="mt-8">
        <SectionHead title={needle ? `Albums · ${shownAlbums.length}` : "Albums"} />
        {shownAlbums.length
          ? <Grow list={shownAlbums} className={ALBUM_GRID}>{(c) => <AlbumCard key={c.key} c={c} />}</Grow>
          : <p className="py-10 text-[13px] text-muted">No matches.</p>}
      </section>

      {!needle && (
        <section className="mt-12">
          <SectionHead title="Artists" />
          <Grow list={artists} step={120} className="grid grid-cols-1 gap-x-8 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {(a) => (
              <Link key={a.name} to={artistPath(a.name)} className="group flex items-baseline justify-between gap-3 border-b border-line py-2">
                <span className="truncate text-[13px] text-fg/90 group-hover:text-white">{a.name}</span>
                <span className="shrink-0 font-mono text-[11px] text-faint tabular">
                  {a.albums ? `${a.albums} ${a.albums === 1 ? "album" : "albums"}` : `${a.tracks} ${a.tracks === 1 ? "track" : "tracks"}`}
                </span>
              </Link>
            )}
          </Grow>
        </section>
      )}
    </>
  );
}
