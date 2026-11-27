import { useEffect, useMemo, useRef, useState, type PointerEvent, type ReactNode } from "react";
import { Grow } from "../lib/grow";
import { cx } from "../lib/format";

export interface Group<T> { id: string; label: string; short?: string; items: T[] }

type Row<T> = { head: Group<T> } | { item: T };

/** A long grid under headings (A, B, C… or decades), with a rail of the headings
 *  down the right edge that jumps to them: tap, or drag along it on a phone. */
export function Grouped<T>({ groups, className, children }: {
  groups: Group<T>[]; className: string; children: (item: T) => ReactNode;
}) {
  const rows = useMemo(() => groups.flatMap<Row<T>>((g) => [{ head: g }, ...g.items.map((item) => ({ item }))]), [groups]);
  const at = useMemo(() => new Map(rows.flatMap((r, i) => ("head" in r ? [[r.head.id, i] as const] : []))), [rows]);
  const [min, setMin] = useState(0);
  const [target, setTarget] = useState<string | null>(null);
  const total = rows.length - groups.length;

  useEffect(() => {   // after the rows up to it are on the page
    if (!target) return;
    document.getElementById(`g-${target}`)?.scrollIntoView({ block: "start" });
    setTarget(null);
  }, [target]);

  const jump = (id: string) => {
    setMin((m) => Math.max(m, (at.get(id) ?? 0) + 60));
    setTarget(id);
  };

  return (
    <>
      <Grow list={rows} min={min} className={className}>
        {(r) => "head" in r
          ? <h3 key={`h-${r.head.id}`} id={`g-${r.head.id}`}
              className="col-span-full -mb-4 scroll-mt-16 border-b border-line pb-1.5 font-mono text-[11px] text-faint first:mt-0">
              {r.head.label}
            </h3>
          : children(r.item)}
      </Grow>
      {groups.length >= 4 && total >= 24 && <Rail groups={groups} onJump={jump} />}
    </>
  );
}

function Rail<T>({ groups, onJump }: { groups: Group<T>[]; onJump: (id: string) => void }) {
  const rail = useRef<HTMLElement>(null);
  const [drag, setDrag] = useState<string | null>(null);
  const last = useRef<string | null>(null);

  const pick = (e: PointerEvent) => {
    // the heading level with the finger, or the nearest (anywhere along the rail, even off it sideways)
    let id: string | undefined, best = Infinity;
    for (const el of rail.current?.querySelectorAll<HTMLElement>("[data-g]") ?? []) {
      const r = el.getBoundingClientRect();
      const d = e.clientY < r.top ? r.top - e.clientY : e.clientY > r.bottom ? e.clientY - r.bottom : 0;
      if (d < best) { best = d; id = el.dataset.g; }
    }
    if (!id || id === last.current) return;
    last.current = id;
    if (e.pointerType !== "mouse") setDrag(id);
    onJump(id);
  };

  return (
    <>
      <div className="pointer-events-none fixed right-0 z-30 flex items-center
        top-[calc(env(safe-area-inset-top)+4.5rem)] bottom-[calc(var(--tabbar)+var(--mini)+1rem)]
        sm:right-1.5 sm:top-20 sm:bottom-[calc(var(--mini)+2rem)]">
        <nav ref={rail} aria-label="Jump to"
          onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); last.current = null; pick(e); }}
          onPointerMove={(e) => { if (e.currentTarget.hasPointerCapture(e.pointerId)) pick(e); }}
          onPointerUp={() => setDrag(null)} onPointerCancel={() => setDrag(null)}
          className="pointer-events-auto flex max-h-full w-4 touch-none select-none flex-col py-2 sm:w-6">
          {groups.map((g) => (
            <button key={g.id} type="button" data-g={g.id} tabIndex={-1} aria-label={g.label}
              className={cx("flex h-[18px] min-h-0 w-full shrink items-center justify-center font-mono text-[10px] leading-none transition-colors",
                drag === g.id ? "text-fg" : "text-faint hover:text-fg")}>
              {g.short ?? g.label}
            </button>
          ))}
        </nav>
      </div>
      {drag && (
        <div className="pointer-events-none fixed inset-0 z-30 grid place-items-center">
          <span className="grid min-w-20 place-items-center rounded-sm bg-raised/95 px-3 py-4 text-3xl font-medium text-fg shadow-xl shadow-black/50">
            {groups.find((g) => g.id === drag)?.label}
          </span>
        </div>
      )}
    </>
  );
}
