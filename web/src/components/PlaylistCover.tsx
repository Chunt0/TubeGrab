import { ListMusic } from "lucide-react";
import { playlistCoverUrl, thumbUrl, type Playlist, type Tile } from "../api";
import { cx } from "../lib/format";

/** Square playlist art: an uploaded picture, a chosen item, or a 2×2 mosaic of the first items. */
export function PlaylistCover({ p, className }: { p: Playlist; className?: string }) {
  const img = (t: Tile, extra?: string) => (
    <div key={t.name} className={cx("size-full", extra)} style={t.color ? { backgroundColor: t.color } : undefined}>
      <img src={thumbUrl(t.name, t.tv)} alt="" loading="lazy" decoding="async" className="size-full object-cover" />
    </div>
  );
  let body;
  if (p.cover_upload) body = <img src={playlistCoverUrl(p)} alt="" loading="lazy" className="size-full object-cover" />;
  else if (p.cover_tile) body = img(p.cover_tile);
  else if (p.mosaic.length >= 4) body = <div className="grid size-full grid-cols-2 grid-rows-2 gap-px bg-line">{p.mosaic.slice(0, 4).map((t) => img(t))}</div>;
  else if (p.mosaic.length) body = img(p.mosaic[0]);
  else body = <div className="grid size-full place-items-center text-faint"><ListMusic className="size-1/4" strokeWidth={1.4} /></div>;
  return <div className={cx("aspect-square overflow-hidden rounded-sm bg-surface", className)}>{body}</div>;
}
