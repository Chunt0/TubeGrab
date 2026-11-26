import { useEffect, useMemo } from "react";
import { ArrowLeft } from "lucide-react";
import { useStore } from "../store";
import { Link, useRouter, watchPath } from "../router";
import { api } from "../api";
import { bytes, longDuration } from "../lib/format";
import { AlbumCard } from "../components/CollectionCard";
import { ALBUM_GRID, SectionHead } from "./Music";

export function ArtistPage({ name }: { name: string }) {
  const { library } = useStore();
  const { navigate } = useRouter();
  // albums newest first, singles last (also the play order)
  const albums = useMemo(
    () => (library?.collections ?? []).filter((c) => c.section === "Music" && c.artist === name)
      .sort((a, b) => (a.kind === "singles" ? 1 : 0) - (b.kind === "singles" ? 1 : 0) || (b.year ?? "").localeCompare(a.year ?? "")),
    [library, name],
  );
  const tracks = albums.reduce((a, c) => a + c.count, 0);
  const time = albums.reduce((a, c) => a + c.duration, 0);
  const size = albums.reduce((a, c) => a + c.size, 0);

  useEffect(() => { if (library?.ready && !albums.length) navigate("/music", true); }, [library, albums.length, navigate]);
  useEffect(() => { document.title = name; return () => { document.title = "Library"; }; }, [name]);
  if (!albums.length) return null;

  const shuffle = async () => {
    const n = await api.random({ artist: name }).catch(() => null);
    if (n) navigate(watchPath(n));
  };

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
            {albums.filter((c) => c.kind === "album").length} albums · {tracks} tracks · {longDuration(time)} · {bytes(size)}
          </p>
        </div>
        <div className="flex gap-2">
          <Link to={watchPath(albums[0].first)} className="flex h-8 items-center rounded-sm bg-fg px-3 text-[13px] font-medium text-ink hover:bg-white">Play all</Link>
          {tracks > 1 && (
            <button onClick={shuffle} className="flex h-8 items-center rounded-sm border border-line-strong px-3 text-[13px] hover:bg-white/5">Shuffle</button>
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
