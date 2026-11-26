export function bytes(n: number | null | undefined): string {
  if (n == null || !isFinite(n)) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n >= 100 || i === 0 ? 0 : 1)} ${u[i]}`;
}

export function duration(sec: number | null | undefined): string {
  if (sec == null || !isFinite(sec)) return "";
  const s = Math.round(sec);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  const mm = h ? String(m).padStart(2, "0") : String(m);
  return `${h ? h + ":" : ""}${mm}:${String(r).padStart(2, "0")}`;
}

export function longDuration(sec: number): string {
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m`;
}

export function eta(sec: number | null | undefined): string {
  if (sec == null) return "—";
  return duration(sec);
}

const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
export function ago(tsSeconds: number): string {
  const diff = tsSeconds - Date.now() / 1000;
  const steps: [number, Intl.RelativeTimeFormatUnit][] = [
    [60, "second"], [60, "minute"], [24, "hour"], [7, "day"], [4.345, "week"], [12, "month"], [Infinity, "year"],
  ];
  let v = diff;
  for (const [n, unit] of steps) {
    if (Math.abs(v) < n) return rtf.format(Math.round(v), unit);
    v /= n;
  }
  return "";
}

export function hostOf(url: string): string {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; }
}

export const FORMAT_LABEL: Record<string, string> = {
  video: "Video", best: "Video", "1080p": "Video", "720p": "Video", "480p": "Video", audio: "Audio",
};

/** "Coldsteel - Topic" → "Coldsteel" (YouTube's auto-generated artist channels) */
export const cleanChannel = (s: string | null | undefined) => (s ?? "").replace(/ - Topic$/, "");

/** yt-dlp upload_date "20251016" → "Oct 16, 2025" */
export function uploadDate(d: string | null | undefined): string {
  if (!d || d.length !== 8) return "";
  const dt = new Date(Number(d.slice(0, 4)), Number(d.slice(4, 6)) - 1, Number(d.slice(6, 8)));
  return dt.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function resLabel(h: number | null | undefined): string {
  if (!h) return "";
  if (h >= 2000) return "4K";
  if (h >= 1300) return "1440p";
  return `${h}p`;
}

export const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");
