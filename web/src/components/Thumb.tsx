import { useState } from "react";
import { Check } from "lucide-react";
import { resumable, thumbUrl, type LibraryFile } from "../api";
import { useStore } from "../store";
import { cx } from "../lib/format";

/** Video thumbnail or audio cover art, with watch progress for long-form items. */
export function Thumb({ file, className, eager }: { file: LibraryFile; className?: string; eager?: boolean }) {
  const [loaded, setLoaded] = useState(false);
  const { progress } = useStore();
  const p = resumable(file) ? progress[file.name] : undefined;
  const frac = p && !p.watched && file.duration ? Math.min(1, p.pos / file.duration) : 0;
  return (
    <div className={cx("relative overflow-hidden bg-surface", className)}>
      <img
        src={thumbUrl(file.name, file.art)}
        alt=""
        loading={eager ? "eager" : "lazy"}
        decoding="async"
        ref={(el) => { if (el?.complete && el.naturalWidth > 0 && !loaded) setLoaded(true); }}
        onLoad={() => setLoaded(true)}
        onError={() => setLoaded(true)}
        className={cx("size-full object-cover transition-opacity duration-300", loaded ? (p?.watched ? "opacity-50" : "opacity-100") : "opacity-0")}
      />
      {p?.watched && (
        <span className="absolute left-1 top-1 grid size-4 place-items-center rounded-sm bg-black/75 text-white" title="Watched">
          <Check className="size-3" strokeWidth={3} />
        </span>
      )}
      {frac > 0.01 && (
        <div className="absolute inset-x-0 bottom-0 h-[3px] bg-white/25">
          <div className="h-full bg-white" style={{ width: `${frac * 100}%` }} />
        </div>
      )}
    </div>
  );
}
