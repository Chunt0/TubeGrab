import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Film, Image as ImageIcon, ListPlus, Music, Pencil, Trash2, Upload } from "lucide-react";
import { Confirm, Menu, MenuDivider, MenuItem } from "../components/ui";
import { CoverPicker } from "../components/CoverPicker";
import { useStore, useWatchedCount } from "../store";
import { Link, artistPath, collectionPath, useRouter, watchPath } from "../router";
import { api, collectionCoverUrl, urls, type Collection, type LibraryFile } from "../api";
import { bytes, longDuration } from "../lib/format";
import { VideoCard } from "../components/VideoCard";
import { KIND_LABEL } from "../components/CollectionCard";
import { EditCollection } from "../components/EditCollection";
import { TrackRow } from "./Music";
import { usePaged } from "../lib/cache";

const GRID = "grid grid-cols-1 gap-x-4 gap-y-7 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5";

const DELETE_LABEL: Record<Collection["kind"], string> = {
  album: "Delete album…", singles: "Delete singles…", channel: "Delete channel…", playlist: "Delete all videos…", folder: "Delete folder…",
};

function Actions({ c, onEdit, onCover, onDelete, onMove }:
  { c: Collection; onEdit: () => void; onCover: () => void; onDelete: () => void; onMove: () => void }) {
  const { navigate } = useRouter();
  const { openAddToPlaylist, toast } = useStore();
  const toMusic = c.section !== "Music";
  const shuffle = async () => {
    const n = await api.random({ dir: c.key }).catch(() => null);
    if (n) navigate(watchPath(n));
  };
  const addAll = async () => {
    try {
      openAddToPlaylist(await api.names(c.key), c.kind === "singles" ? `${c.artist} · Singles` : c.title);
    } catch (e) {
      toast({ kind: "bad", title: "Couldn't load the list", body: e instanceof Error ? e.message : String(e) });
    }
  };
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Link to={watchPath(c.first)} className="flex h-8 items-center rounded-sm bg-fg px-3 text-[13px] font-medium text-ink hover:bg-white">
        {c.kind === "channel" ? "Play latest" : "Play all"}
      </Link>
      {c.count > 1 && (
        <button onClick={shuffle} className="flex h-8 items-center rounded-sm border border-line-strong px-3 text-[13px] hover:bg-white/5">Shuffle</button>
      )}
      <Menu>
        {(close) => (
          <>
            <MenuItem icon={ListPlus} onClick={() => { close(); addAll(); }}>
              {c.count > 1 ? "Add all to playlist…" : "Add to playlist…"}
            </MenuItem>
            <MenuItem icon={ImageIcon} onClick={() => { close(); onCover(); }}>Change picture…</MenuItem>
            <MenuItem icon={Pencil} onClick={() => { close(); onEdit(); }}>Edit details…</MenuItem>
            <MenuItem icon={toMusic ? Music : Film} onClick={() => { close(); onMove(); }}>
              {toMusic ? "Move to Music" : "Move to Videos"}
            </MenuItem>
            <MenuDivider />
            <MenuItem icon={Trash2} danger onClick={() => { close(); onDelete(); }}>{DELETE_LABEL[c.kind]}</MenuItem>
          </>
        )}
      </Menu>
    </div>
  );
}

export function CollectionPage({ name: key }: { name: string }) {
  const { library, setCover, uploadCollectionCover, deleteCollection, move, toast } = useStore();
  const upload = useRef<HTMLInputElement>(null);
  const onUpload = async (file: File | undefined) => {
    if (!file || !c) return;
    try {
      await uploadCollectionCover(c.key, file);
      setPicking(false);
    } catch (e) {
      toast({ kind: "bad", title: "Couldn't use that picture", body: e instanceof Error ? e.message : String(e) });
    }
  };
  const { navigate } = useRouter();
  const [editing, setEditing] = useState(false);
  const [picking, setPicking] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [moving, setMoving] = useState(false);
  const c = library?.collections.find((x) => x.key === key);
  const music = c ? c.section === "Music" : key.startsWith("Music/");
  // albums: every track at once (they're short); channels: as you scroll
  const { items, sentinel } = usePaged<LibraryFile>(urls.files({ dir: key }), music ? 1000 : 60);
  const watched = useWatchedCount(key);

  useEffect(() => { if (library?.ready && !c && !editing && !moving) navigate(music ? "/music" : "/videos", true); }, [library, c, editing, moving, music, navigate]);
  useEffect(() => {
    if (c) document.title = c.artist ? `${c.title} · ${c.artist}` : c.title;
    return () => { document.title = "Library"; };
  }, [c]);
  if (!c) return null;

  const onMove = async () => {
    if (moving) return;
    setMoving(true);
    const to = await move({ key: c.key }, music ? "Videos" : "Music");
    setMoving(false);
    if (to) navigate(collectionPath(to), true);
  };

  const back = music
    ? { to: c.artist ? artistPath(c.artist) : "/music", label: c.artist ?? "Music" }
    : { to: "/videos", label: "Videos" };
  const stats = (
    <p className="mt-2 font-mono text-[11px] text-faint tabular">
      {c.count} {music ? (c.count === 1 ? "track" : "tracks") : c.count === 1 ? "item" : "items"}
      {watched != null && <> · {watched} watched</>}
      {c.duration > 0 && <> · {longDuration(c.duration)}</>} · {bytes(c.size)}
    </p>
  );

  return (
    <div className="pt-6">
      <Link to={back.to} className="inline-flex items-center gap-1.5 text-xs text-muted hover:text-fg">
        <ArrowLeft className="size-3.5" /> {back.label}
      </Link>

      {music ? (
        // album: square cover beside a numbered track list
        <div className="mt-6 grid gap-8 md:grid-cols-[minmax(0,280px)_minmax(0,1fr)] lg:gap-12">
          <div>
            {collectionCoverUrl(c) && <img src={collectionCoverUrl(c)!} alt="" className="aspect-square w-full max-w-[280px] rounded-sm object-cover" />}
            <p className="mt-5 text-xs text-muted">{KIND_LABEL[c.kind]}{c.year && <> · {c.year}</>}</p>
            <h1 className="mt-1 text-2xl font-medium leading-tight tracking-tight">{c.kind === "singles" ? "Singles" : c.title}</h1>
            {c.artist && <Link to={artistPath(c.artist)} className="mt-1 block text-[15px] text-fg/80 hover:text-white hover:underline">{c.artist}</Link>}
            {stats}
            <div className="mt-5"><Actions c={c} onEdit={() => setEditing(true)} onCover={() => setPicking(true)} onDelete={() => setDeleting(true)} onMove={onMove} /></div>
          </div>
          <div className="min-w-0 md:pt-1">
            <div className="border-t border-line">
              {items.map((f, i) => (
                <TrackRow key={f.name} f={f} n={c.kind === "album" ? f.index ?? i + 1 : i + 1} showAlbum={false} />
              ))}
              {!items.length && Array.from({ length: Math.min(c.count, 12) }, (_, i) => <div key={i} className="h-[45px] border-b border-line" />)}
            </div>
          </div>
        </div>
      ) : (
        <>
          <header className="mt-6 flex flex-col gap-5 border-b border-line pb-6 sm:flex-row sm:items-end">
            {collectionCoverUrl(c) && <img src={collectionCoverUrl(c)!} alt="" className="aspect-video w-full rounded-sm object-cover sm:w-56" />}
            <div className="min-w-0 flex-1">
              <p className="text-xs text-muted">{KIND_LABEL[c.kind]}</p>
              <h1 className="mt-1 text-2xl font-medium tracking-tight sm:text-3xl">{c.title}</h1>
              {stats}
            </div>
            <Actions c={c} onEdit={() => setEditing(true)} onCover={() => setPicking(true)} onDelete={() => setDeleting(true)} onMove={onMove} />
          </header>
          <div className={`mt-6 ${GRID}`}>
            {items.map((f, i) => <VideoCard key={f.name} file={f} position={c.kind === "playlist" ? f.index ?? i + 1 : null} showChannel={c.kind === "playlist"} />)}
          </div>
          {sentinel}
        </>
      )}

      <EditCollection c={c} open={editing} onClose={() => setEditing(false)} />
      <Confirm open={deleting} onClose={() => setDeleting(false)} confirmLabel="Delete"
        title={c.kind === "album" ? "Delete this album?" : c.kind === "singles" ? "Delete these singles?" : `Delete all ${c.count === 1 ? "files" : `${c.count} files`} in “${c.title}”?`}
        body={<>{c.kind === "singles" ? `${c.artist} · Singles` : `“${c.title}”`} · {c.count} {c.count === 1 ? "file" : "files"} · {bytes(c.size)} will be permanently removed.</>}
        onConfirm={async () => {
          try {
            await deleteCollection(c);
            navigate(back.to, true);
          } catch (e) {
            toast({ kind: "bad", title: "Couldn't delete", body: e instanceof Error ? e.message : String(e) });
          }
        }} />
      <input ref={upload} type="file" accept="image/*" className="hidden"
        onChange={(e) => { onUpload(e.target.files?.[0]); e.target.value = ""; }} />
      <CoverPicker open={picking} onClose={() => setPicking(false)} items={items} current={c.cover_url ? null : c.covers[0]?.name ?? null}
        extra={
          <button type="button" onClick={() => upload.current?.click()}
            className="flex h-8 items-center gap-1.5 rounded-sm border border-line-strong px-3 text-[13px] hover:bg-white/5">
            <Upload className="size-3.5" /> Upload picture…
          </button>
        }
        onPick={(f) => setCover(c.key, f.name)} square={music} numbered={c.kind === "album" || c.kind === "playlist"} />
    </div>
  );
}
