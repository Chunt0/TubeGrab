import { Heart as HeartIcon } from "lucide-react";
import type { FavoriteKind, LibraryFile, Mark } from "../api";
import { useMark } from "../store";
import { cx } from "../lib/format";

type Props = {
  kind: FavoriteKind; id: string; file?: LibraryFile; boxed?: boolean;
  /** in list rows: only shown when on, or on hover */
  quiet?: boolean; className?: string;
};

/** Lucide's heart-crack, with the crack drawn over the heart (in the page colour when
 * filled): Lucide draws the crack first, so a filled one looks just like a heart. */
export function BrokenHeart({ className, fill = "none", strokeWidth = 1.75 }: { className?: string; fill?: string; strokeWidth?: number }) {
  const filled = fill !== "none";
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth={strokeWidth}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path fill={fill} d="M13.508 20.313a2 2 0 0 1-3 .019L5 15c-1.5-1.5-3-3.2-3-5.5a5.5 5.5 0 0 1 9.591-3.677.6.6 0 0 0 .818.001A5.5 5.5 0 0 1 22 9.5c0 2.29-1.5 4-3 5.5z" />
      <path stroke={filled ? "var(--color-ink)" : "currentColor"} strokeWidth={filled ? 2 : strokeWidth}
        d="M12.409 5.824c-.702.792-1.15 1.496-1.415 2.166l2.153 2.156a.5.5 0 0 1 0 .707l-2.293 2.293a.5.5 0 0 0 0 .707L12 15" />
    </svg>
  );
}

const LOOK = {
  fav: { Icon: HeartIcon, on: "Remove from favorites", off: "Add to favorites" },
  dislike: { Icon: BrokenHeart, on: "Remove dislike", off: "Dislike" },
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
