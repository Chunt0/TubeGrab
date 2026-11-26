import { useEffect, useState, type FormEvent } from "react";
import type { Collection } from "../api";
import { useStore } from "../store";
import { useRouter, collectionPath } from "../router";
import { Button, Modal } from "./ui";

function Field({ label, value, onChange, placeholder, inputMode, autoFocus }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string;
  inputMode?: "numeric"; autoFocus?: boolean;
}) {
  return (
    <label className="block">
      <span className="text-xs text-muted">{label}</span>
      <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
        inputMode={inputMode} autoFocus={autoFocus} spellCheck={false}
        className="mt-1 h-9 w-full rounded-sm border border-line-strong bg-transparent px-2.5 text-sm outline-none focus:border-fg/60" />
    </label>
  );
}

/** Fix a name YouTube got wrong. Renames the folder (and retags music). */
export function EditCollection({ c, open, onClose }: { c: Collection; open: boolean; onClose: () => void }) {
  const { editCollection } = useStore();
  const { navigate } = useRouter();
  const music = c.section === "Music";
  const [title, setTitle] = useState(c.title);
  const [artist, setArtist] = useState(c.artist ?? "");
  const [year, setYear] = useState(c.year ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) { setTitle(c.title); setArtist(c.artist ?? ""); setYear(c.year ?? ""); setError(null); }
  }, [open, c]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const key = await editCollection(c.key, music
        ? { artist, ...(c.kind === "album" ? { title, year } : {}) }
        : { title });
      onClose();
      navigate(collectionPath(key), true);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} className="max-w-md">
      <form onSubmit={submit} className="p-5">
        <h2 className="text-[15px] font-medium">Edit {c.kind === "singles" ? "singles" : c.kind}</h2>
        <div className="mt-5 space-y-3">
          {music && <Field label="Artist" value={artist} onChange={setArtist} autoFocus />}
          {(!music || c.kind === "album") && (
            <Field label={music ? "Album" : "Name"} value={title} onChange={setTitle} autoFocus={!music} />
          )}
          {music && c.kind === "album" && (
            <Field label="Year" value={year} onChange={setYear} placeholder="optional" inputMode="numeric" />
          )}
        </div>
        {error && <p className="mt-3 text-xs text-bad">{error}</p>}
        <div className="mt-6 flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={busy}>{busy ? "Saving…" : "Save"}</Button>
        </div>
      </form>
    </Modal>
  );
}
