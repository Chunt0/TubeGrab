import { useEffect, useRef, useState } from "react";
import { ArrowLeft, GripVertical, Image as ImageIcon, Pencil, RotateCcw, Trash2, Upload, X } from "lucide-react";
import { useStore } from "../store";
import { playlistFiles, type LibraryFile, type Playlist } from "../api";
import { Link, useRouter, watchPath } from "../router";
import { bytes, cleanChannel, cx, duration, longDuration } from "../lib/format";
import { Thumb } from "../components/Thumb";
import { PlaylistCover } from "../components/PlaylistCover";
import { PlaylistDialog } from "../components/PlaylistDialog";
import { CoverPicker } from "../components/CoverPicker";
import { Confirm, Menu, MenuDivider, MenuItem } from "../components/ui";

type Drag = { from: number; over: number; y0: number; dy: number; tops: number[]; heights: number[] };

/** Drag-to-reorder list (handle-driven, so scrolling and tapping rows still work on phones). */
function SortableList({ p, items, onReorder, onRemove }: {
  p: Playlist; items: LibraryFile[];
  onReorder: (names: string[]) => void; onRemove: (f: LibraryFile) => void;
}) {
  const rows = useRef<(HTMLDivElement | null)[]>([]);
  const [drag, setDrag] = useState<Drag | null>(null);

  const start = (i: number, e: React.PointerEvent) => {
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    const rects = rows.current.slice(0, items.length).map((r) => r!.getBoundingClientRect());
    setDrag({ from: i, over: i, y0: e.pageY, dy: 0, tops: rects.map((r) => r.top + scrollY), heights: rects.map((r) => r.height) });
  };
  const move = (e: React.PointerEvent) => {
    if (!drag) return;
    // nudge the page when dragging near the top/bottom edge
    if (e.clientY < 90) scrollBy(0, -12); else if (e.clientY > innerHeight - 140) scrollBy(0, 12);
    const dy = e.pageY - drag.y0;
    const mid = drag.tops[drag.from] + drag.heights[drag.from] / 2 + dy;
    let over = 0;
    drag.tops.forEach((t, j) => { if (j !== drag.from && t + drag.heights[j] / 2 < mid) over++; });
    setDrag({ ...drag, dy, over });
  };
  const end = () => {
    if (drag && drag.over !== drag.from) {
      const names = items.map((f) => f.name);
      const [m] = names.splice(drag.from, 1);
      names.splice(drag.over, 0, m);
      onReorder(names);
    }
    setDrag(null);
  };

  const shift = (i: number) => {
    if (!drag || i === drag.from) return 0;
    const h = drag.heights[drag.from];
    if (drag.from < drag.over && i > drag.from && i <= drag.over) return -h;
    if (drag.from > drag.over && i >= drag.over && i < drag.from) return h;
    return 0;
  };

  return (
    <div className={cx("border-t border-line", drag && "select-none")}>
      {items.map((f, i) => {
        const dragging = drag?.from === i;
        const square = f.kind === "audio";
        return (
          <div
            key={f.name}
            ref={(el) => { rows.current[i] = el; }}
            style={{ transform: `translateY(${dragging ? drag!.dy : shift(i)}px)` }}
            className={cx("group relative flex items-center gap-2 border-b border-line bg-ink py-1.5",
              dragging ? "z-10 bg-raised shadow-xl shadow-black/60" : drag && "transition-transform duration-150")}
          >
            <button
              type="button" aria-label="Drag to reorder" title="Drag to reorder"
              onPointerDown={(e) => start(i, e)} onPointerMove={move} onPointerUp={end} onPointerCancel={end}
              className="grid h-10 w-6 shrink-0 cursor-grab touch-none place-items-center text-faint hover:text-fg active:cursor-grabbing"
            >
              <GripVertical className="size-4" />
            </button>
            <span className="w-5 shrink-0 text-right font-mono text-[11px] text-faint tabular">{i + 1}</span>
            <Link to={watchPath(f.name, p.id)} className="flex min-w-0 flex-1 items-center gap-3">
              <Thumb file={f} className={cx("shrink-0 rounded-sm", square ? "size-10" : "aspect-video w-[72px]")} />
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px] text-fg/90 group-hover:text-white">{f.title}</div>
                <div className="truncate text-[11.5px] text-muted">
                  {f.section === "Music" ? [f.artist, f.album].filter(Boolean).join(" · ") : cleanChannel(f.uploader) || bytes(f.size)}
                </div>
              </div>
              <span className="hidden font-mono text-[11px] text-muted tabular sm:block">{duration(f.duration)}</span>
            </Link>
            <button type="button" onClick={() => onRemove(f)} aria-label="Remove from playlist" title="Remove from playlist"
              className="grid size-8 shrink-0 place-items-center text-faint opacity-0 transition-opacity hover:text-fg group-hover:opacity-100 focus:opacity-100 [@media(hover:none)]:opacity-100">
              <X className="size-4" />
            </button>
          </div>
        );
      })}
    </div>
  );
}

export function PlaylistPage({ id }: { id: string }) {
  const { playlists, files, filesLoaded, updatePlaylist, deletePlaylist, uploadPlaylistCover, toast } = useStore();
  const { navigate } = useRouter();
  const [editing, setEditing] = useState(false);
  const [picking, setPicking] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const upload = useRef<HTMLInputElement>(null);
  const p = playlists.find((x) => x.id === id);
  const items = p ? playlistFiles(p, files) : [];

  useEffect(() => { if (filesLoaded && !p) navigate("/playlists", true); }, [filesLoaded, p, navigate]);
  useEffect(() => {
    if (p) document.title = p.title;
    return () => { document.title = "Library"; };
  }, [p]);
  if (!p) return null;

  const time = items.reduce((t, f) => t + (f.duration ?? 0), 0);
  const custom = !!(p.cover_upload || p.cover_item);
  const fail = (e: unknown) => toast({ kind: "bad", title: "Couldn't save", body: e instanceof Error ? e.message : String(e) });

  const onUpload = async (file: File | undefined) => {
    if (!file) return;
    try {
      await uploadPlaylistCover(p.id, file);
      setPicking(false);
      toast({ kind: "ok", title: "Cover updated" });
    } catch (e) { fail(e); }
  };

  return (
    <div className="pt-6">
      <Link to="/playlists" className="inline-flex items-center gap-1.5 text-xs text-muted hover:text-fg">
        <ArrowLeft className="size-3.5" /> Playlists
      </Link>

      <div className="mt-6 grid gap-8 md:grid-cols-[minmax(0,280px)_minmax(0,1fr)] lg:gap-12">
        <div>
          <PlaylistCover p={p} files={files} className="w-full max-w-[280px]" />
          <p className="mt-5 text-xs text-muted">Playlist</p>
          <h1 className="mt-1 break-words text-2xl font-medium leading-tight tracking-tight">{p.title}</h1>
          {p.description && <p className="mt-2 whitespace-pre-line break-words text-[13px] leading-relaxed text-fg/70">{p.description}</p>}
          <p className="mt-2 font-mono text-[11px] text-faint tabular">
            {items.length} {items.length === 1 ? "item" : "items"}{time > 0 && <> · {longDuration(time)}</>}
          </p>
          <div className="mt-5 flex flex-wrap items-center gap-2">
            {items[0] && (
              <Link to={watchPath(items[0].name, p.id)} className="flex h-8 items-center rounded-sm bg-fg px-3 text-[13px] font-medium text-ink hover:bg-white">
                Play all
              </Link>
            )}
            {items.length > 1 && (
              <button onClick={() => navigate(watchPath(items[Math.floor(Math.random() * items.length)].name, p.id))}
                className="flex h-8 items-center rounded-sm border border-line-strong px-3 text-[13px] hover:bg-white/5">Shuffle</button>
            )}
            <Menu>
              {(close) => (
                <>
                  <MenuItem icon={Pencil} onClick={() => { close(); setEditing(true); }}>Edit details…</MenuItem>
                  {items.length > 0 && <MenuItem icon={ImageIcon} onClick={() => { close(); setPicking(true); }}>Choose cover…</MenuItem>}
                  <MenuItem icon={Upload} onClick={() => { close(); upload.current?.click(); }}>Upload picture…</MenuItem>
                  {custom && (
                    <MenuItem icon={RotateCcw} onClick={() => { close(); updatePlaylist(p.id, { cover_item: null }).catch(fail); }}>
                      Use automatic cover
                    </MenuItem>
                  )}
                  <MenuDivider />
                  <MenuItem icon={Trash2} danger onClick={() => { close(); setConfirmDelete(true); }}>Delete playlist…</MenuItem>
                </>
              )}
            </Menu>
          </div>
        </div>

        <div className="min-w-0 md:pt-1">
          {items.length ? (
            <SortableList
              p={p} items={items}
              onReorder={(names) => updatePlaylist(p.id, { items: names }).catch(fail)}
              onRemove={(f) => updatePlaylist(p.id, { items: p.items.filter((n) => n !== f.name) })
                .then(() => toast({ kind: "info", title: "Removed from playlist", body: f.title })).catch(fail)}
            />
          ) : (
            <div className="border-t border-line py-10 text-[13px] text-muted">Empty</div>
          )}
        </div>
      </div>

      <input ref={upload} type="file" accept="image/*" className="hidden"
        onChange={(e) => { onUpload(e.target.files?.[0]); e.target.value = ""; }} />
      <PlaylistDialog open={editing} onClose={() => setEditing(false)} title="Edit playlist" submitLabel="Save"
        initial={{ title: p.title, description: p.description }}
        onSubmit={(v) => updatePlaylist(p.id, v)} />
      <CoverPicker open={picking} onClose={() => setPicking(false)} items={items} square
        current={p.cover_upload ? null : p.cover_item}
        onPick={(f) => updatePlaylist(p.id, { cover_item: f.name })}
        extra={
          <button type="button" onClick={() => upload.current?.click()}
            className="flex h-8 items-center gap-1.5 rounded-sm border border-line-strong px-3 text-[13px] hover:bg-white/5">
            <Upload className="size-3.5" /> Upload picture…
          </button>
        } />
      <Confirm open={confirmDelete} onClose={() => setConfirmDelete(false)} title="Delete this playlist?"
        body={<>“{p.title}”</>}
        onConfirm={async () => { await deletePlaylist(p.id); navigate("/playlists", true); }} />
    </div>
  );
}
