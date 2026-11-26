import type { LibraryFile } from "../api";
import { Link, watchPath } from "../router";
import { ago, bytes, cleanChannel, duration } from "../lib/format";
import { Thumb } from "./Thumb";
import { ItemMenu } from "./ItemMenu";

export function VideoCard({ file, position, showChannel = true }:
  { file: LibraryFile; index?: number; position?: number | null; showChannel?: boolean }) {
  const channel = file.section === "Music" ? file.artist : cleanChannel(file.uploader);
  return (
    <div className="group relative">
    <Link to={watchPath(file.name)} className="block" title={file.title}>
      <div className="relative aspect-video overflow-hidden rounded-sm">
        <Thumb file={file} className="size-full transition-opacity group-hover:opacity-80" />
        {file.duration != null && (
          <span className="absolute bottom-1.5 right-1.5 bg-black/80 px-1 py-px font-mono text-[10.5px] text-white tabular">
            {duration(file.duration)}
          </span>
        )}
        {file.kind === "audio" && (
          <span className="absolute bottom-1.5 left-1.5 bg-black/80 px-1 py-px font-mono text-[10.5px] uppercase text-white/80">
            audio
          </span>
        )}
      </div>
      <div className="mt-2.5 flex gap-2.5">
        {position != null && <span className="w-5 shrink-0 pt-px text-right font-mono text-xs text-faint tabular">{position}</span>}
        <div className="min-w-0 pr-7">
          <h3 className="line-clamp-2 text-[13.5px] leading-snug text-fg/90 group-hover:text-white">{file.title}</h3>
          <p className="mt-1 truncate text-xs text-muted">
            {showChannel && channel && <>{channel} · </>}{bytes(file.size)} · {ago(file.mtime)}
          </p>
        </div>
      </div>
    </Link>
    <div className="absolute -right-1.5 bottom-0"><ItemMenu file={file} /></div>
    </div>
  );
}
