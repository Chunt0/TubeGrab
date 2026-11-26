import { useState } from "react";
import { isActive, type Job } from "../api";
import { useStore } from "../store";
import { Link, watchPath } from "../router";
import { FORMAT_LABEL, bytes, cx, eta, hostOf } from "../lib/format";

const STATUS: Record<Job["status"], { label: string; dot: string }> = {
  queued: { label: "Queued", dot: "bg-muted" },
  downloading: { label: "Downloading", dot: "bg-fg animate-pulse" },
  complete: { label: "Done", dot: "bg-ok" },
  error: { label: "Failed", dot: "bg-bad" },
  cancelled: { label: "Stopped", dot: "bg-faint" },
};

export function JobCard({ job }: { job: Job }) {
  const { cancel, files } = useStore();
  const [stopping, setStopping] = useState(false);
  const active = isActive(job);
  const pct = job.status === "complete" ? 100 : job.percent;
  const s = STATUS[job.status];
  const libNames = new Set(files.map((f) => f.name));
  const landed = job.files.filter((n) => libNames.has(n));
  const multi = job.playlist_count && job.playlist_count > 1;

  return (
    <div className="border-b border-line py-4">
      <div className="flex items-start gap-3">
        <span className={cx("mt-[7px] size-1.5 shrink-0 rounded-full", s.dot)} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13.5px]">{job.title || job.url}</div>
          <div className="mt-0.5 flex flex-wrap gap-x-2 text-xs text-muted">
            <span>{stopping && active ? "Stopping…" : s.label}</span>
            <span className="text-faint">·</span><span>{FORMAT_LABEL[job.format]}</span>
            {multi && active && <><span className="text-faint">·</span><span className="font-mono tabular">{job.playlist_index ?? "?"}/{job.playlist_count}</span></>}
            {job.entry_count && job.entry_count > 1 && !active && <><span className="text-faint">·</span><span>{job.entry_count} items</span></>}
            <span className="text-faint">·</span><span>{hostOf(job.url)}</span>
          </div>
        </div>
        {active && (
          <button
            onClick={() => { setStopping(true); cancel(job.id).catch(() => setStopping(false)); }}
            disabled={stopping}
            className="text-xs text-muted hover:text-bad disabled:opacity-40"
          >
            Stop
          </button>
        )}
      </div>

      {active && (
        <div className="mt-3 pl-[18px]">
          <div className="relative h-px overflow-hidden bg-line-strong">
            {pct != null
              ? <div className="h-full bg-fg transition-[width] duration-700 ease-out" style={{ width: `${pct}%` }} />
              : <div className="absolute inset-y-0 w-1/3 bg-fg animate-shimmer" />}
          </div>
          <div className="mt-2 flex flex-wrap gap-x-5 font-mono text-[11px] text-muted tabular">
            <span className="text-fg">{pct != null ? `${pct.toFixed(1)}%` : "—"}</span>
            <span>{job.speed ? `${bytes(job.speed)}/s` : "—"}</span>
            <span>ETA {eta(job.eta)}</span>
            <span>{job.total ? `${bytes(job.downloaded)} / ${bytes(job.total)}` : bytes(job.downloaded)}</span>
          </div>
          {job.current_file && <div className="mt-1 truncate font-mono text-[11px] text-faint">{job.current_file}</div>}
        </div>
      )}

      {job.status === "error" && job.error && (
        <p className="mt-2 pl-[18px] font-mono text-[11.5px] leading-relaxed text-bad/90">{job.error}</p>
      )}

      {!active && landed.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 pl-[18px]">
          {landed.slice(0, 4).map((n) => (
            <Link key={n} to={watchPath(n)} className="max-w-full truncate text-xs text-muted hover:text-fg hover:underline">
              ▸ {n.split("/").pop()!.replace(/^\d{2,4} - /, "").replace(/\s*\[[A-Za-z0-9_-]{11}\]\.\w+$/, "")}
            </Link>
          ))}
          {landed.length > 4 && <span className="text-xs text-faint">+{landed.length - 4}</span>}
        </div>
      )}
    </div>
  );
}
