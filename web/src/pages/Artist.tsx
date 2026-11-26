import { useEffect, useMemo } from "react";
import { ArrowLeft } from "lucide-react";
import { useStore } from "../store";
import { Link, useRouter, watchPath } from "../router";
import { collectionOrder, filesIn } from "../api";
import { bytes, longDuration } from "../lib/format";
import { AlbumCard } from "../components/CollectionCard";
import { ALBUM_GRID, SectionHead } from "./Music";

export function ArtistPage({ name }: { name: string }) {
  const { files, collections, filesLoaded } = useStore();
  const { navigate } = useRouter();
  const albums = useMemo(
    () => collections.filter((c) => c.section === "Music" && c.artist === name)
      .sort((a, b) => (a.kind === "singles" ? 1 : 0) - (b.kind === "singles" ? 1 : 0) || (b.year ?? "").localeCompare(a.year ?? "")),
    [collections, name],
  );
  // play order: albums newest-first in track order, then singles
  const queue = useMemo(() => albums.flatMap((c) => collectionOrder(c, filesIn(c.key, files))), [albums, files]);
  const time = queue.reduce((a, f) => a + (f.duration ?? 0), 0);

  useEffect(() => { if (filesLoaded && !albums.length) navigate("/music", true); }, [filesLoaded, albums.length, navigate]);
  useEffect(() => { document.title = name; return () => { document.title = "Library"; }; }, [name]);
  if (!albums.length) return null;

  return (
    <div className="pt-6">
      <Link to="/music" className="inline-flex items-center gap-1.5 text-xs text-muted hover:text-fg">
        <ArrowLeft className="size-3.5" /> Music
      </Link>
      <header className="mt-6 flex flex-wrap items-end gap-5 border-b border-line pb-6">
        <div className="min-w-0 flex-1">
          <p className="text-xs text-muted">Artist</p>
          <h1 className="mt-1 text-2xl font-medium tracking-tight sm:text-3xl">{name}</h1>
          <p className="mt-2 font-mono text-[11px] text-faint tabular">
            {albums.filter((c) => c.kind === "album").length} albums · {queue.length} tracks · {longDuration(time)} · {bytes(queue.reduce((a, f) => a + f.size, 0))}
          </p>
        </div>
        <div className="flex gap-2">
          {queue[0] && (
            <Link to={watchPath(queue[0].name)} className="flex h-8 items-center rounded-sm bg-fg px-3 text-[13px] font-medium text-ink hover:bg-white">Play all</Link>
          )}
          {queue.length > 1 && (
            <button onClick={() => navigate(watchPath(queue[Math.floor(Math.random() * queue.length)].name))}
              className="flex h-8 items-center rounded-sm border border-line-strong px-3 text-[13px] hover:bg-white/5">Shuffle</button>
          )}
        </div>
      </header>
      <section className="mt-6">
        <SectionHead title="Albums" />
        <div className={ALBUM_GRID}>{albums.map((c) => <AlbumCard key={c.key} c={c} showArtist={false} />)}</div>
      </section>
    </div>
  );
}
