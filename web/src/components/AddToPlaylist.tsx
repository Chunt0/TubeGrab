import { useEffect, useState, type FormEvent } from "react";
import { Check, Minus, Plus } from "lucide-react";
import { useStore } from "../store";
import { cx } from "../lib/format";
import { Modal } from "./ui";
import { PlaylistCover } from "./PlaylistCover";

/** Toggle the targeted item(s) in any of your playlists, or start a new one with them. */
export function AddToPlaylist() {
  const { addTarget, closeAddToPlaylist, playlists, files, updatePlaylist, createPlaylist, toast } = useStore();
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const names = addTarget ?? [];
  const one = names.length === 1 ? files.find((f) => f.name === names[0]) : null;

  useEffect(() => { if (addTarget) { setNewName(""); setError(null); } }, [addTarget]);

  const toggle = async (id: string) => {
    const p = playlists.find((x) => x.id === id);
    if (!p) return;
    const has = names.filter((n) => p.items.includes(n));
    const all = has.length === names.length;
    setBusy(id); setError(null);
    try {
      await updatePlaylist(id, { items: all ? p.items.filter((n) => !names.includes(n)) : [...p.items, ...names.filter((n) => !p.items.includes(n))] });
      toast({ kind: all ? "info" : "ok", title: all ? `Removed from ${p.title}` : `Added to ${p.title}` });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const create = async (e: FormEvent) => {
    e.preventDefault();
    if (!newName.trim()) return;
    setBusy("new"); setError(null);
    try {
      const p = await createPlaylist(newName.trim(), names);
      toast({ kind: "ok", title: `Added to ${p.title}` });
      closeAddToPlaylist();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Modal open={!!addTarget} onClose={closeAddToPlaylist} className="max-w-md">
      <div className="p-5">
        <h2 className="text-[13px] text-muted">Add to playlist</h2>
        <p className="mt-1 truncate pr-8 text-[12px] text-faint">{one ? one.title : `${names.length} items`}</p>

        <div className="-mx-2 mt-4 max-h-[45dvh] overflow-y-auto">
          {playlists.map((p) => {
            const has = names.filter((n) => p.items.includes(n)).length;
            const state = has === 0 ? "none" : has === names.length ? "all" : "some";
            return (
              <button key={p.id} type="button" onClick={() => toggle(p.id)} disabled={!!busy}
                className={cx("flex w-full items-center gap-3 rounded-sm px-2 py-1.5 text-left hover:bg-hover disabled:cursor-wait", busy === p.id && "animate-pulse")}>
                <PlaylistCover p={p} files={files} className="size-10 shrink-0" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13px] text-fg/90">{p.title}</div>
                  <div className="text-[11.5px] text-muted">{p.items.length} {p.items.length === 1 ? "item" : "items"}</div>
                </div>
                <span className={cx("grid size-5 shrink-0 place-items-center rounded-sm border",
                  state === "none" ? "border-line-strong" : "border-fg bg-fg text-ink")}>
                  {state === "all" && <Check className="size-3.5" strokeWidth={3} />}
                  {state === "some" && <Minus className="size-3.5" strokeWidth={3} />}
                </span>
              </button>
            );
          })}
        </div>

        <form onSubmit={create} className="mt-4 flex gap-2 border-t border-line pt-4">
          <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="New playlist name" maxLength={120}
            autoFocus={!playlists.length}
            className="h-9 min-w-0 flex-1 rounded-sm border border-line-strong bg-transparent px-2.5 text-sm outline-none focus:border-fg/60" />
          <button type="submit" disabled={!newName.trim() || !!busy}
            className="flex h-9 items-center gap-1.5 rounded-sm bg-fg px-3 text-[13px] font-medium text-ink hover:bg-white disabled:opacity-40">
            <Plus className="size-3.5" /> Create
          </button>
        </form>
        {error && <p className="mt-3 text-[12px] text-bad">{error}</p>}
      </div>
    </Modal>
  );
}
