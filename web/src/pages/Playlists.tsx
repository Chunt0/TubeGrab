import { useState } from "react";
import { Plus } from "lucide-react";
import { useStore } from "../store";
import { playlistFiles } from "../api";
import { Link, playlistPath, useRouter } from "../router";
import { longDuration } from "../lib/format";
import { PlaylistCover } from "../components/PlaylistCover";
import { PlaylistDialog } from "../components/PlaylistDialog";
import { ALBUM_GRID } from "./Music";

export function Playlists() {
  const { playlists, files, filesLoaded, createPlaylist } = useStore();
  const { navigate } = useRouter();
  const [creating, setCreating] = useState(false);

  const newButton = (
    <button onClick={() => setCreating(true)}
      className="flex h-8 items-center gap-1.5 rounded-sm border border-line-strong px-3 text-[13px] hover:bg-white/5">
      <Plus className="size-3.5" /> New playlist
    </button>
  );
  const dialog = (
    <PlaylistDialog open={creating} onClose={() => setCreating(false)} title="New playlist" submitLabel="Create"
      onSubmit={async (v) => { const p = await createPlaylist(v.title, [], v.description); navigate(playlistPath(p.id)); }} />
  );

  if (!filesLoaded) return <div className={`mt-10 ${ALBUM_GRID}`}>{Array.from({ length: 6 }, (_, i) => <div key={i} className="aspect-square rounded-sm bg-surface" />)}</div>;

  if (!playlists.length) {
    return (
      <section className="mx-auto mt-[16vh] max-w-xl">
        <h1 className="text-[15px] font-medium">No playlists yet</h1>
        <div className="mt-6">{newButton}</div>
        {dialog}
      </section>
    );
  }

  return (
    <>
      <div className="flex items-center gap-4 pt-6">
        <div className="mr-auto flex items-baseline gap-3">
          <h1 className="text-[15px] font-medium">Playlists</h1>
          <span className="font-mono text-[11px] text-faint tabular">{playlists.length}</span>
        </div>
        {newButton}
      </div>

      <div className={`mt-8 ${ALBUM_GRID}`}>
        {playlists.map((p) => {
          const items = playlistFiles(p, files);
          const time = items.reduce((t, f) => t + (f.duration ?? 0), 0);
          return (
            <Link key={p.id} to={playlistPath(p.id)} className="group block min-w-0">
              <PlaylistCover p={p} files={files} className="transition-opacity group-hover:opacity-80" />
              <div className="mt-2.5 truncate text-[13.5px] text-fg/90 group-hover:text-white">{p.title}</div>
              <div className="mt-0.5 truncate text-xs text-muted">
                {items.length} {items.length === 1 ? "item" : "items"}{time > 0 && <> · {longDuration(time)}</>}
              </div>
            </Link>
          );
        })}
      </div>
      {dialog}
    </>
  );
}
