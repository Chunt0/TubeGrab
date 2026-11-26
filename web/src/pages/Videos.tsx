import { useEffect, useMemo, useState } from "react";
import { useStore } from "../store";
import { api, type Collection, type LibraryFile, type SearchResult } from "../api";
import { Link, watchPath } from "../router";
import { Thumb } from "../components/Thumb";
import { bytes, duration, longDuration } from "../lib/format";
import { VideoCard } from "../components/VideoCard";
import { GrabForm } from "../components/GrabForm";
import { CollectionCard } from "../components/CollectionCard";
import { Tab } from "../components/ui";
import { Grow } from "../lib/showMore";
import { SearchBox, SectionHead as Head } from "./Music";

type Kind = "all" | "video" | "audio";
type Sort = "new" | "old" | "title" | "size" | "long";
const SORTS: { id: Sort; label: string }[] = [
  { id: "new", label: "Newest" },
  { id: "old", label: "Oldest" },
  { id: "title", label: "A–Z" },
  { id: "long", label: "Longest" },
  { id: "size", label: "Largest" },
];

const GRID = "grid grid-cols-1 gap-x-4 gap-y-7 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5";

const SectionHead = Head;

function Collections({ items }: { items: Collection[] }) {
  const [all, setAll] = useState(false);
  const LIMIT = 5;
  return (
    <section className="mt-8">
      <SectionHead title="Collections">
        {items.length > LIMIT && (
          <button onClick={() => setAll((a) => !a)} className="text-xs text-muted hover:text-fg">
            {all ? "Less" : `All ${items.length}`}
          </button>
        )}
      </SectionHead>
      <div className={GRID}>
        {(all ? items : items.slice(0, LIMIT)).map((c) => <CollectionCard key={c.key} c={c} />)}
      </div>
    </section>
  );
}

/** Words spoken in your videos (from their captions). */
function SpokenResults({ q, files, emptyText }: { q: string; files: LibraryFile[]; emptyText?: boolean }) {
  const [results, setResults] = useState<SearchResult[] | null>(null);
  useEffect(() => {
    const term = q.trim();
    if (term.length < 3) { setResults(null); return; }
    let live = true;
    const t = setTimeout(() => api.search(term).then((r) => { if (live) setResults(r); }).catch(() => {}), 250);
    return () => { live = false; clearTimeout(t); };
  }, [q]);
  const by = useMemo(() => new Map(files.map((f) => [f.name, f])), [files]);
  const shown = (results ?? []).filter((r) => by.has(r.name));
  if (!shown.length) {
    return emptyText && (results !== null || q.trim().length < 3)
      ? <p className="py-16 text-center text-[13px] text-muted">No matches.</p> : null;
  }
  const mark = (text: string) => {
    const i = text.toLowerCase().indexOf(q.trim().toLowerCase());
    if (i < 0) return text;
    return <>{text.slice(0, i)}<mark className="bg-transparent text-fg">{text.slice(i, i + q.trim().length)}</mark>{text.slice(i + q.trim().length)}</>;
  };
  return (
    <section className="mt-10">
      <SectionHead title="Said in videos" />
      <div className="space-y-5">
        {shown.map((r) => {
          const f = by.get(r.name)!;
          return (
            <div key={r.name} className="flex gap-4">
              <Link to={watchPath(f.name)} className="w-32 shrink-0 sm:w-40"><Thumb file={f} className="aspect-video rounded-sm" /></Link>
              <div className="min-w-0 flex-1">
                <Link to={watchPath(f.name)} className="line-clamp-1 text-[13.5px] text-fg/90 hover:text-white">{f.title}</Link>
                <div className="mt-1 space-y-0.5">
                  {r.hits.map((h) => (
                    <Link key={h.t} to={`${watchPath(f.name)}?t=${Math.floor(h.t)}`} className="flex gap-3 text-[12.5px] text-muted hover:text-fg">
                      <span className="w-10 shrink-0 font-mono text-[11px] text-faint tabular">{duration(h.t) || "0:00"}</span>
                      <span className="line-clamp-2 min-w-0">{mark(h.text)}</span>
                    </Link>
                  ))}
                  {r.count > r.hits.length && <p className="pl-[3.25rem] text-[11px] text-faint">+{r.count - r.hits.length} more</p>}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function Empty() {
  return (
    <section className="mx-auto mt-[16vh] max-w-xl">
      <h1 className="text-[15px] font-medium">No videos yet</h1>
      <div className="mt-6"><GrabForm /></div>
    </section>
  );
}

export function Videos() {
  const store = useStore();
  const { filesLoaded } = store;
  const files = useMemo(() => store.files.filter((f) => f.section !== "Music"), [store.files]);
  const collections = useMemo(() => store.collections.filter((c) => c.section !== "Music"), [store.collections]);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState<Kind>("all");
  const [sort, setSort] = useState<Sort>("new");

  const stats = useMemo(() => ({
    size: files.reduce((a, f) => a + f.size, 0),
    time: files.reduce((a, f) => a + (f.duration ?? 0), 0),
    audio: files.filter((f) => f.kind === "audio").length,
  }), [files]);

  const list = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const out = files.filter((f) => (kind === "all" || f.kind === kind)
      && (!needle || [f.title, f.uploader, f.collection].some((x) => x?.toLowerCase().includes(needle))));
    const cmp: Record<Sort, (a: LibraryFile, b: LibraryFile) => number> = {
      new: (a, b) => b.mtime - a.mtime,
      old: (a, b) => a.mtime - b.mtime,
      title: (a, b) => a.title.localeCompare(b.title),
      size: (a, b) => b.size - a.size,
      long: (a, b) => (b.duration ?? 0) - (a.duration ?? 0),
    };
    return out.sort(cmp[sort]);
  }, [files, q, kind, sort]);

  if (!filesLoaded) {
    return (
      <div className={`mt-10 ${GRID}`}>
        {Array.from({ length: 10 }, (_, i) => <div key={i} className="aspect-video rounded-sm bg-surface" />)}
      </div>
    );
  }
  if (!files.length) return <Empty />;
  const filtering = q || kind !== "all";

  return (
    <>
      {/* toolbar */}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3 pt-6">
        <div className="order-0 flex w-full items-baseline gap-3 sm:mr-auto sm:w-auto">
          <h1 className="text-[15px] font-medium">Videos</h1>
          <span className="font-mono text-[11px] text-faint tabular">
            {files.length} · {stats.time > 0 && <>{longDuration(stats.time)} · </>}{bytes(stats.size)}
          </span>
        </div>
        {stats.audio > 0 && stats.audio < files.length && (
          <div className="order-2 flex items-center gap-4 sm:order-none">
            {(["all", "video", "audio"] as Kind[]).map((k) => (
              <Tab key={k} active={kind === k} onClick={() => setKind(k)} className="capitalize">{k}</Tab>
            ))}
          </div>
        )}
        <select value={sort} onChange={(e) => setSort(e.target.value as Sort)} aria-label="Sort"
          className="order-3 ml-auto h-8 sm:order-none sm:ml-0 cursor-pointer bg-transparent text-[13px] text-muted outline-none hover:text-fg">
          {SORTS.map((s) => <option key={s.id} value={s.id} className="bg-surface text-fg">{s.label}</option>)}
        </select>
        <div className="order-1 w-full sm:order-none sm:w-auto"><SearchBox q={q} setQ={setQ} /></div>
      </div>

      {!filtering && collections.length > 0 && <Collections items={collections} />}

      {(list.length > 0 || !q) && (
        <section className="mt-10">
          <SectionHead title={filtering ? `${list.length} result${list.length === 1 ? "" : "s"}` : "Recent"} />
          {list.length
            ? <Grow list={list} className={GRID}>{(f) => <VideoCard key={f.name} file={f} />}</Grow>
            : <p className="py-16 text-center text-[13px] text-muted">No matches.</p>}
        </section>
      )}
      {q && <SpokenResults q={q} files={files} emptyText={!list.length} />}
    </>
  );
}
