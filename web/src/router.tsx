import { createContext, useCallback, useContext, useEffect, useState, type ReactNode, type MouseEvent } from "react";

interface Loc { path: string; search: string }
interface Nav { navigate: (to: string, replace?: boolean, opts?: { keepScroll?: boolean }) => void }
const RouterCtx = createContext<Loc & Nav>({ path: "/", search: "", navigate: () => {} });

const here = (): Loc => ({ path: location.pathname, search: location.search });

// Back and forward return to where you were on the page: each history entry
// gets a key, and the scroll position is kept per key.
const scrolls = new Map<string, number>();
let seq = 0;
const newKey = () => `${Date.now().toString(36)}-${++seq}`;
const entryKey = () => (history.state as { k?: string } | null)?.k ?? "";

/** Scroll to `y` once the page is tall enough: long grids render a screenful at a
 *  time, and each scroll towards the end makes more of them appear. Gives up after
 *  a few seconds, or as soon as you scroll yourself. */
function restore(y: number) {
  let stop = false;
  const off = () => { stop = true; };
  const events = ["wheel", "touchstart", "keydown", "pointerdown"] as const;
  events.forEach((e) => addEventListener(e, off, { once: true, passive: true }));
  const until = performance.now() + 4000;
  const step = () => {
    if (stop) return;
    scrollTo(0, y);
    if (Math.abs(scrollY - y) > 2 && performance.now() < until) requestAnimationFrame(step);
    else events.forEach((e) => removeEventListener(e, off));
  };
  requestAnimationFrame(step);
}

export function Router({ children }: { children: ReactNode }) {
  const [loc, setLoc] = useState(here);
  useEffect(() => {
    history.scrollRestoration = "manual";
    if (!entryKey()) history.replaceState({ k: newKey() }, "");
    const onPop = () => {
      setLoc(here());
      restore(scrolls.get(entryKey()) ?? 0);
    };
    addEventListener("popstate", onPop);
    return () => removeEventListener("popstate", onPop);
  }, []);
  const navigate = useCallback((to: string, replace = false, opts: { keepScroll?: boolean } = {}) => {
    if (to === location.pathname + location.search) return;
    if (replace) {
      history.replaceState({ k: entryKey() || newKey() }, "", to);
    } else {
      scrolls.set(entryKey(), scrollY);
      history.pushState({ k: newKey() }, "", to);
    }
    setLoc(here());
    if (!opts.keepScroll) window.scrollTo({ top: 0 });
  }, []);
  return <RouterCtx.Provider value={{ ...loc, navigate }}>{children}</RouterCtx.Provider>;
}

export const useRouter = () => useContext(RouterCtx);

/** One query parameter of the current URL, and a setter that changes it in place
 *  (no new history entry, so back still leaves the page). */
export function useParam(name: string): [string | null, (v: string | null) => void] {
  const { path, search, navigate } = useRouter();
  const value = new URLSearchParams(search).get(name);
  const set = useCallback((v: string | null) => {
    const p = new URLSearchParams(location.search);
    if (v) p.set(name, v); else p.delete(name);
    const s = p.toString();
    navigate(`${path}${s ? `?${s}` : ""}`, true, { keepScroll: true });
  }, [name, path, navigate]);
  return [value, set];
}

export function Link({ to, className, children, title, onClick: after }:
  { to: string; className?: string; children: ReactNode; title?: string; onClick?: () => void }) {
  const { navigate } = useRouter();
  const onClick = (e: MouseEvent) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    navigate(to);
    after?.();
  };
  return <a href={to} onClick={onClick} className={className} title={title}>{children}</a>;
}

/** `list`: play inside one of your playlists (its order drives next / previous). */
export const watchPath = (name: string, list?: string | null) =>
  `/watch/${encodeURIComponent(name)}${list ? `?list=${encodeURIComponent(list)}` : ""}`;
export const collectionPath = (key: string) => `/c/${encodeURIComponent(key)}`;
export const artistPath = (name: string) => `/music/artist/${encodeURIComponent(name)}`;
export const playlistPath = (id: string) => `/p/${encodeURIComponent(id)}`;
