import { useEffect, useRef, useState, type ReactNode } from "react";

/** Render a long list a screenful at a time: more appear as you scroll near the end. */
export function useShowMore<T>(list: T[], step = 48) {
  const [n, setN] = useState(step);
  const end = useRef<HTMLDivElement>(null);
  const more = n < list.length;
  useEffect(() => {
    const el = end.current;
    if (!el || !more) return;
    const io = new IntersectionObserver((es) => { if (es[0]?.isIntersecting) setN((x) => x + step); }, { rootMargin: "1200px 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, [more, n, step]);
  return { shown: more ? list.slice(0, n) : list, end: more ? <div ref={end} aria-hidden className="h-px" /> : null };
}

/** A list/grid container that renders its items with useShowMore. */
export function Grow<T>({ list, className, step, children }: {
  list: T[]; className?: string; step?: number; children: (item: T, i: number) => ReactNode;
}) {
  const { shown, end } = useShowMore(list, step);
  return <><div className={className}>{shown.map(children)}</div>{end}</>;
}
