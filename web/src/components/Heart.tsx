import { Heart as HeartIcon } from "lucide-react";
import type { FavoriteKind, LibraryFile } from "../api";
import { useFavorite } from "../store";
import { cx } from "../lib/format";

/** Heart an artist, album or song. `boxed`: outlined like the buttons beside it. */
export function Heart({ kind, id, file, boxed, quiet, className }: {
  kind: FavoriteKind; id: string; file?: LibraryFile; boxed?: boolean;
  /** in list rows: only shown when hearted, or on hover */
  quiet?: boolean; className?: string;
}) {
  const [on, toggle] = useFavorite(kind, id, file);
  const label = on ? "Remove from favorites" : "Add to favorites";
  return (
    <button type="button" onClick={(e) => { e.preventDefault(); e.stopPropagation(); toggle(); }}
      aria-pressed={on} aria-label={label} title={label}
      className={cx("grid shrink-0 place-items-center transition-colors",
        boxed ? "size-8 rounded-sm border border-line-strong hover:bg-white/5" : "size-8",
        on ? "text-fg" : "text-faint hover:text-fg",
        quiet && !on && "opacity-0 focus-visible:opacity-100 group-hover:opacity-100 [@media(hover:none)]:invisible", className)}>
      <HeartIcon className="size-4" fill={on ? "currentColor" : "none"} strokeWidth={on ? 1.5 : 1.75} />
    </button>
  );
}
