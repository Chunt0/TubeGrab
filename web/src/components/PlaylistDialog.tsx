import { useEffect, useState, type FormEvent } from "react";
import { Button, Modal } from "./ui";

/** Name + description, for creating or editing a playlist. */
export function PlaylistDialog({ open, onClose, title: heading, initial, submitLabel, onSubmit }: {
  open: boolean;
  onClose: () => void;
  title: string;
  initial?: { title: string; description: string };
  submitLabel: string;
  onSubmit: (v: { title: string; description: string }) => Promise<unknown>;
}) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) { setTitle(initial?.title ?? ""); setDescription(initial?.description ?? ""); setError(null); }
  }, [open, initial?.title, initial?.description]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!title.trim()) { setError("Give the playlist a name."); return; }
    setBusy(true); setError(null);
    try {
      await onSubmit({ title: title.trim(), description: description.trim() });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} className="max-w-md">
      <form onSubmit={submit} className="p-5">
        <h2 className="text-[13px] text-muted">{heading}</h2>
        <label className="mt-4 block">
          <span className="text-xs text-muted">Name</span>
          <input value={title} onChange={(e) => setTitle(e.target.value)} autoFocus maxLength={120} placeholder="My playlist"
            className="mt-1 h-9 w-full rounded-sm border border-line-strong bg-transparent px-2.5 text-sm outline-none focus:border-fg/60" />
        </label>
        <label className="mt-3 block">
          <span className="text-xs text-muted">Description <span className="text-faint">(optional)</span></span>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3} maxLength={1000}
            className="mt-1 w-full resize-none rounded-sm border border-line-strong bg-transparent px-2.5 py-2 text-sm outline-none focus:border-fg/60" />
        </label>
        {error && <p className="mt-3 text-[12px] text-bad">{error}</p>}
        <div className="mt-5 flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={busy}>{submitLabel}</Button>
        </div>
      </form>
    </Modal>
  );
}
