import { Heart as HeartIcon, ThumbsDown } from "lucide-react";
import type { FavoriteKind, LibraryFile, Mark } from "../api";
import { useMark } from "../store";
import { cx } from "../lib/format";

type Props = {
  kind: FavoriteKind; id: string; file?: LibraryFile; boxed?: boolean;
  /** in list rows: only shown when on, or on hover */
  quiet?: boolean; className?: string;
};

const LOOK = {
  fav: { Icon: HeartIcon, on: "Remove from favorites", off: "Add to favorites" },
  dislike: { Icon: ThumbsDown, on: "Remove dislike", off: "Dislike" },
};

function MarkButton({ mark, kind, id, file, boxed, quiet, className }: Props & { mark: Mark }) {
  const [on, toggle] = useMark(mark, kind, id, file);
  const { Icon, ...labels } = LOOK[mark];
  const label = on ? labels.on : labels.off;
  return (
    <button type="button" onClick={(e) => { e.preventDefault(); e.stopPropagation(); toggle(); }}
      aria-pressed={on} aria-label={label} title={label}
      className={cx("grid shrink-0 place-items-center transition-colors",
        boxed ? "size-8 rounded-sm border border-line-strong hover:bg-white/5" : "size-8",
        on ? "text-fg" : "text-faint hover:text-fg",
        quiet && !on && "opacity-0 focus-visible:opacity-100 group-hover:opacity-100 [@media(hover:none)]:invisible", className)}>
      <Icon className="size-4" fill={on ? "currentColor" : "none"} strokeWidth={on ? 1.5 : 1.75} />
    </button>
  );
}

/** Heart an artist, album or song. `boxed`: outlined like the buttons beside it. */
export const Heart = (p: Props) => <MarkButton mark="fav" {...p} />;

/** Dislike an artist, album or song: only recorded (for agents), nothing is hidden. */
export const Dislike = (p: Props) => <MarkButton mark="dislike" {...p} />;
