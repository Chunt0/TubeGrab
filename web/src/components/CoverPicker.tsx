import { useState, type ReactNode } from "react";
import { Check } from "lucide-react";
import { ownThumbUrl, type LibraryFile } from "../api";
import { cx } from "../lib/format";
import { Modal } from "./ui";

/** Pick which item's thumbnail represents an album / collection / playlist. */
export function CoverPicker({ open, onClose, items, current, onPick, square, numbered, extra }: {
  open: boolean;
  onClose: () => void;
  items: LibraryFile[];
  /** name of the item currently used as the cover */
  current: string | null;
  onPick: (f: LibraryFile) => Promise<unknown>;
  square: boolean;
  numbered?: boolean;
  /** e.g. an "Upload picture" button */
  extra?: ReactNode;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const pick = async (f: LibraryFile) => {
    if (f.name === current) { onClose(); return; }
    setBusy(f.name);
    setError(null);
    try {
      await onPick(f);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Modal open={open} onClose={onClose} className="max-w-3xl">
      <div className="p-5">
        <h2 className="text-[13px] text-muted">Change picture</h2>
        {extra && <div className="mt-4">{extra}</div>}
        {error && <p className="mt-3 text-[12px] text-bad">{error}</p>}
        <div className={cx("mt-4 grid max-h-[60dvh] gap-3 overflow-y-auto", square ? "grid-cols-3 sm:grid-cols-4 md:grid-cols-5" : "grid-cols-2 sm:grid-cols-3")}>
          {items.map((f, i) => {
            const selected = f.name === current;
            return (
              <button key={f.name} type="button" onClick={() => pick(f)} disabled={!!busy}
                className="group min-w-0 text-left disabled:cursor-wait">
                <div className={cx("relative overflow-hidden rounded-sm bg-surface outline-offset-2 transition-[outline-color]",
                  square ? "aspect-square" : "aspect-video",
                  selected ? "outline-2 outline-fg" : "outline-1 outline-transparent group-hover:outline-line-strong",
                  busy === f.name && "animate-pulse")}>
                  <img src={ownThumbUrl(f.name, f.tv)} alt="" loading="lazy" decoding="async" className="size-full object-cover" />
                  {selected && (
                    <span className="absolute right-1 top-1 grid size-5 place-items-center rounded-sm bg-fg text-ink">
                      <Check className="size-3.5" strokeWidth={3} />
                    </span>
                  )}
                </div>
                <div className="mt-1.5 truncate text-[11.5px] text-muted group-hover:text-fg">
                  {numbered && <span className="font-mono text-faint tabular">{f.index ?? i + 1} </span>}
                  {f.title}
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </Modal>
  );
}
