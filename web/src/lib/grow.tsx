import { useEffect, useRef, useState, type ReactNode } from "react";

/** Render a long in-memory list a screenful at a time: more appear as you scroll
 *  near the end. `min`: at least this many (to jump to an item further down). */
export function useShowMore<T>(list: T[], step = 48, min = 0) {
  const [n, setN] = useState(step);
  const count = Math.max(n, min);
  const end = useRef<HTMLDivElement>(null);
  const more = count < list.length;
  useEffect(() => {
    const el = end.current;
    if (!el || !more) return;
    const io = new IntersectionObserver((es) => { if (es[0]?.isIntersecting) setN(count + step); }, { rootMargin: "1200px 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, [more, count, step]);
  return { shown: more ? list.slice(0, count) : list, end: more ? <div ref={end} aria-hidden className="h-px" /> : null };
}

/** A list/grid container that renders its items with useShowMore. */
export function Grow<T>({ list, className, step, min, children }: {
  list: T[]; className?: string; step?: number; min?: number; children: (item: T, i: number) => ReactNode;
}) {
  const { shown, end } = useShowMore(list, step, min);
  return <><div className={className}>{shown.map(children)}</div>{end}</>;
}
