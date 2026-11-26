import { ListMusic } from "lucide-react";
import { playlistCoverUrl, playlistFiles, thumbUrl, type LibraryFile, type Playlist } from "../api";
import { cx } from "../lib/format";

/** Square playlist art: an uploaded picture, a chosen item, or a 2×2 mosaic of the first items. */
export function PlaylistCover({ p, files, className }: { p: Playlist; files: LibraryFile[]; className?: string }) {
  const items = playlistFiles(p, files);
  // one tile per album (tracks of an album share its art), every video its own
  const seen = new Set<string>();
  const mosaic = items.filter((f) => {
    const k = f.section === "Music" && f.collection ? f.collection : f.name;
    return seen.has(k) ? false : (seen.add(k), true);
  });
  const img = (f: LibraryFile, extra?: string) => (
    <img key={f.name} src={thumbUrl(f.name, f.art)} alt="" loading="lazy" className={cx("size-full object-cover", extra)} />
  );
  let body;
  if (p.cover_upload) body = <img src={playlistCoverUrl(p)} alt="" loading="lazy" className="size-full object-cover" />;
  else if (p.cover_item && items.some((f) => f.name === p.cover_item)) body = img(items.find((f) => f.name === p.cover_item)!);
  else if (mosaic.length >= 4) body = <div className="grid size-full grid-cols-2 grid-rows-2 gap-px bg-line">{mosaic.slice(0, 4).map((f) => img(f))}</div>;
  else if (items.length) body = img(items[0]);
  else body = <div className="grid size-full place-items-center text-faint"><ListMusic className="size-1/4" strokeWidth={1.4} /></div>;
  return <div className={cx("aspect-square overflow-hidden rounded-sm bg-surface", className)}>{body}</div>;
}
