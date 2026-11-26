import { collectionCoverUrl, thumbUrl, type Collection } from "../api";
import { Link, collectionPath } from "../router";
import { longDuration } from "../lib/format";
import { useWatchedCount } from "../store";

export const KIND_LABEL: Record<Collection["kind"], string> = {
  album: "Album", singles: "Singles", playlist: "Playlist", channel: "Channel", folder: "Folder",
};

/** Channel / playlist: main cover + a strip of the next two (16:9). */
export function CollectionCard({ c }: { c: Collection }) {
  const watched = useWatchedCount(c.key);
  const covers = c.cover_url ? c.covers.slice(0, 1) : c.covers.slice(0, 3);   // an uploaded picture stands alone
  const main = collectionCoverUrl(c);
  return (
    <Link to={collectionPath(c.key)} className="group block" title={c.title}>
      <div className={`grid aspect-video gap-px overflow-hidden rounded-sm bg-line ${covers.length > 1 ? "grid-cols-[2fr_1fr]" : ""}`}>
        <div className="bg-surface">
          {main && <img src={main} alt="" loading="lazy" className="size-full object-cover transition-opacity group-hover:opacity-80" />}
        </div>
        {covers.length > 1 && <div className="grid grid-rows-2 gap-px">
          {[1, 2].map((i) => (
            <div key={i} className="bg-surface">
              {covers[i] && <img src={thumbUrl(covers[i])} alt="" loading="lazy" className="size-full object-cover opacity-70 transition-opacity group-hover:opacity-50" />}
            </div>
          ))}
        </div>}
      </div>
      <h3 className="mt-2.5 truncate text-[13.5px] text-fg/90 group-hover:text-white">{c.title}</h3>
      <p className="mt-0.5 text-xs text-muted">
        {KIND_LABEL[c.kind]} · {watched != null ? `${watched}/${c.count} watched` : `${c.count} ${c.count === 1 ? "item" : "items"}`}
        {c.duration > 0 && <> · {longDuration(c.duration)}</>}
      </p>
    </Link>
  );
}

/** Album / singles: square cover. */
export function AlbumCard({ c, showArtist = true }: { c: Collection; showArtist?: boolean }) {
  return (
    <Link to={collectionPath(c.key)} className="group block" title={c.title}>
      <div className="aspect-square overflow-hidden rounded-sm bg-surface">
        {collectionCoverUrl(c) && <img src={collectionCoverUrl(c)!} alt="" loading="lazy" className="size-full object-cover transition-opacity group-hover:opacity-80" />}
      </div>
      <h3 className="mt-2 truncate text-[13.5px] text-fg/90 group-hover:text-white">
        {c.kind === "singles" ? "Singles" : c.title}
      </h3>
      <p className="mt-0.5 truncate text-xs text-muted">
        {showArtist && c.artist ? c.artist : c.kind === "singles" ? `${c.count} ${c.count === 1 ? "track" : "tracks"}` : `${c.count} tracks`}
        {c.year && <> · {c.year}</>}
      </p>
    </Link>
  );
}
