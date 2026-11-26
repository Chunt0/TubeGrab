import { useEffect, useState } from "react";
import { api, type LibraryFile, type Segment } from "../api";

const cache = new Map<string, Promise<Segment[]>>();

/** Captions as timed lines (fetched once per file, shared by CC and the transcript panel). */
export function useTranscript(file: LibraryFile | null | undefined): Segment[] | null {
  const name = file?.subs ? file.name : null;
  const [segs, setSegs] = useState<Segment[] | null>(null);
  useEffect(() => {
    setSegs(null);
    if (!name) return;
    let live = true;
    if (!cache.has(name)) cache.set(name, api.transcript(name).catch(() => { cache.delete(name); return []; }));
    cache.get(name)!.then((s) => { if (live) setSegs(s); });
    return () => { live = false; };
  }, [name]);
  return segs;
}

/** Index of the line being spoken at `t` (or -1). */
export function segmentAt(segs: Segment[], t: number): number {
  let lo = 0, hi = segs.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (segs[mid].start <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return ans >= 0 && t < segs[ans].end + 0.3 ? ans : -1;
}

export function chapterAt(chapters: { start: number }[], t: number): number {
  let i = -1;
  chapters.forEach((c, k) => { if (c.start <= t) i = k; });
  return i;
}
