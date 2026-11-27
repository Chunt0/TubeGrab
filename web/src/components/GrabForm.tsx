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

export const isUrl = (s: string) => /^https?:\/\/\S+$/i.test(s.trim());

/** Format + whole-playlist choice and sending a link off (the form here, and search). */
export function useGrab(onSubmitted?: () => void) {
  const { grab, toast } = useStore();
  const [format, setFormat] = useState<Format>("audio");   // always starts on Audio
  const [playlist, setPlaylist] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (url: string) => {
    const u = url.trim();
    if (!u || busy) return false;
    setBusy(true); setError(null);
    try {
      await grab(u, format, playlist);
      toast({ kind: "info", title: "Queued", body: u });
      onSubmitted?.();
      return true;
    } catch (err) {
      setError((err as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  };
  return { format, setFormat, playlist, setPlaylist, busy, error, setError, submit };
}

export type Grab = ReturnType<typeof useGrab>;

export function GrabOptions({ g, children }: { g: Grab; children?: React.ReactNode }) {
  const current = FORMATS.find((f) => f.id === g.format)!;
  return (
    <>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <div role="radiogroup" aria-label="Type" className="flex items-center gap-4 border-b border-line">
          {FORMATS.map((f) => (
            <Tab key={f.id} role="radio" aria-checked={g.format === f.id} active={g.format === f.id}
              title={f.size} onClick={() => g.setFormat(f.id)}>
              {f.label}
            </Tab>
          ))}
        </div>
        <button type="button" role="switch" aria-checked={g.playlist} onClick={() => g.setPlaylist((p) => !p)}
          className="flex items-center gap-2 text-[13px] text-muted transition-colors hover:text-fg">
          <Switch on={g.playlist} /> Whole playlist
        </button>
        {children}
      </div>
      <p className="mt-2 font-mono text-[11.5px] text-faint">{current.size}</p>
    </>
  );
}

export function GrabForm({ prefill = "", autoFocus, onSubmitted }:
  { prefill?: string; autoFocus?: boolean; onSubmitted?: () => void }) {
  const g = useGrab(onSubmitted);
  const [url, setUrl] = useState(prefill);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => { setUrl(prefill); }, [prefill]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!url.trim()) { input.current?.focus(); return; }
    if (await g.submit(url)) setUrl("");
  }

  return (
    <form onSubmit={submit} className="w-full">
      <div className={cx("flex items-stretch rounded-sm border transition-colors focus-within:border-fg/60",
        g.error ? "border-bad/50" : "border-line-strong")}>
        <input
          ref={input}
          value={url}
          onChange={(e) => { setUrl(e.target.value); g.setError(null); }}
          autoFocus={autoFocus}
          placeholder="Paste a video or playlist URL"
          spellCheck={false}
          autoComplete="off"
          inputMode="url"
          className="h-10 min-w-0 flex-1 bg-transparent px-3 text-sm outline-none placeholder:text-faint"
        />
        <button
          type="submit"
          disabled={g.busy}
          className="flex w-20 shrink-0 items-center justify-center border-l border-line-strong bg-fg text-[13px] font-medium text-ink transition-colors hover:bg-white disabled:opacity-60"
        >
          {g.busy ? <Loader2 className="size-4 animate-spin" /> : "Add"}
        </button>
      </div>
      {g.error && <p className="mt-2 text-xs text-bad">{g.error}</p>}
      <div className="mt-3"><GrabOptions g={g} /></div>
    </form>
  );
}
