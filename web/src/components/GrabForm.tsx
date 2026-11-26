import { useEffect, useRef, useState, type FormEvent } from "react";
import { Loader2 } from "lucide-react";
import type { Format } from "../api";
import { useStore } from "../store";
import { cx } from "../lib/format";
import { Switch, Tab } from "./ui";

const FORMATS: { id: Format; label: string; size: string }[] = [
  { id: "video", label: "Video", size: "720p · ≈ 75–500 MB/h" },
  { id: "audio", label: "Audio", size: "≈ 60 MB/h" },
];

export function GrabForm({ prefill = "", autoFocus, onSubmitted }:
  { prefill?: string; autoFocus?: boolean; onSubmitted?: () => void }) {
  const { grab, toast } = useStore();
  const [url, setUrl] = useState(prefill);
  const [format, setFormat] = useState<Format>("audio");   // always starts on Audio
  const [playlist, setPlaylist] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => { setUrl(prefill); }, [prefill]);

  const current = FORMATS.find((f) => f.id === format)!;

  async function submit(e: FormEvent) {
    e.preventDefault();
    const u = url.trim();
    if (!u) { input.current?.focus(); return; }
    setBusy(true); setError(null);
    try {
      await grab(u, format, playlist);
      toast({ kind: "info", title: "Queued", body: u });
      setUrl("");
      onSubmitted?.();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="w-full">
      <div className={cx("flex items-stretch rounded-sm border transition-colors focus-within:border-fg/60",
        error ? "border-bad/50" : "border-line-strong")}>
        <input
          ref={input}
          value={url}
          onChange={(e) => { setUrl(e.target.value); setError(null); }}
          autoFocus={autoFocus}
          placeholder="Paste a video or playlist URL"
          spellCheck={false}
          autoComplete="off"
          inputMode="url"
          className="h-10 min-w-0 flex-1 bg-transparent px-3 text-sm outline-none placeholder:text-faint"
        />
        <button
          type="submit"
          disabled={busy}
          className="flex w-20 shrink-0 items-center justify-center border-l border-line-strong bg-fg text-[13px] font-medium text-ink transition-colors hover:bg-white disabled:opacity-60"
        >
          {busy ? <Loader2 className="size-4 animate-spin" /> : "Add"}
        </button>
      </div>
      {error && <p className="mt-2 text-xs text-bad">{error}</p>}

      <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-2">
        <div role="radiogroup" aria-label="Type" className="flex items-center gap-4 border-b border-line">
          {FORMATS.map((f) => (
            <Tab key={f.id} role="radio" aria-checked={format === f.id} active={format === f.id}
              title={f.size} onClick={() => setFormat(f.id)}>
              {f.label}
            </Tab>
          ))}
        </div>
        <button type="button" role="switch" aria-checked={playlist} onClick={() => setPlaylist((p) => !p)}
          className="flex items-center gap-2 text-[13px] text-muted transition-colors hover:text-fg">
          <Switch on={playlist} /> Whole playlist
        </button>
      </div>
      <p className="mt-2 font-mono text-[11.5px] text-faint">{current.size}</p>
    </form>
  );
}
