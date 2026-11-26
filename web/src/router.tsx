import { createContext, useCallback, useContext, useEffect, useState, type ReactNode, type MouseEvent } from "react";

interface Loc { path: string; search: string }
const RouterCtx = createContext<Loc & { navigate: (to: string, replace?: boolean) => void }>({
  path: "/", search: "", navigate: () => {},
});

const here = (): Loc => ({ path: location.pathname, search: location.search });

export function Router({ children }: { children: ReactNode }) {
  const [loc, setLoc] = useState(here);
  useEffect(() => {
    const onPop = () => setLoc(here());
    addEventListener("popstate", onPop);
    return () => removeEventListener("popstate", onPop);
  }, []);
  const navigate = useCallback((to: string, replace = false) => {
    if (to === location.pathname + location.search) return;
    history[replace ? "replaceState" : "pushState"](null, "", to);
    setLoc(here());
    window.scrollTo({ top: 0 });
  }, []);
  return <RouterCtx.Provider value={{ ...loc, navigate }}>{children}</RouterCtx.Provider>;
}

export const useRouter = () => useContext(RouterCtx);

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
