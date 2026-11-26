import type { ReactNode } from "react";
import { Film, ListMusic, Loader2, Music2, Plus } from "lucide-react";
import { Link, useRouter } from "../router";
import { useStore } from "../store";
import { cx } from "../lib/format";
import { Kbd } from "./ui";

/** Which nav item a path belongs to (collection / player pages follow their section). */
function useActive(to: string) {
  const { path, search } = useRouter();
  const p = decodeURIComponent(path);
  if (p.startsWith("/p/") || (p.startsWith("/watch/") && search.includes("list="))) return to === "/playlists";
  const section = p.startsWith("/c/") ? p.slice(3).split("/")[0] : p.startsWith("/watch/") ? p.slice(7).split("/")[0] : null;
  return path.startsWith(to)
    || (to === "/music" && section === "Music")
    || (to === "/videos" && !!section && section !== "Music");
}

function NavLink({ to, children, badge }: { to: string; children: ReactNode; badge?: number }) {
  const active = useActive(to);
  return (
    <Link
      to={to}
      className={cx(
        "relative flex h-12 items-center gap-1.5 text-[13px] transition-colors",
        active ? "text-fg after:absolute after:inset-x-0 after:-bottom-px after:h-px after:bg-fg" : "text-muted hover:text-fg",
      )}
    >
      {children}
      {!!badge && <span className="font-mono text-[11px] text-fg tabular">{badge}</span>}
    </Link>
  );
}

function TabLink({ to, icon: Icon, label, badge }: { to: string; icon: typeof Music2; label: string; badge?: number }) {
  const active = useActive(to);
  return (
    <Link to={to} className={cx("relative flex flex-1 flex-col items-center justify-center gap-1 text-[10.5px]", active ? "text-fg" : "text-faint")}>
      <Icon className="size-5" strokeWidth={active ? 2 : 1.6} />
      {label}
      {!!badge && (
        <span className="absolute left-1/2 top-1.5 ml-2 min-w-4 rounded-sm bg-fg px-1 text-center font-mono text-[9.5px] leading-4 text-ink tabular">
          {badge}
        </span>
      )}
    </Link>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const { activeCount, openGrab } = useStore();
  return (
    <div className="min-h-dvh">
      {/* desktop / tablet: top bar */}
      <header className="sticky top-0 z-40 hidden border-b border-line bg-ink/95 backdrop-blur sm:block">
        <div className="mx-auto flex h-12 max-w-[1440px] items-center gap-6 px-8">
          <nav className="flex items-center gap-6">
            <NavLink to="/music">Music</NavLink>
            <NavLink to="/videos">Videos</NavLink>
            <NavLink to="/playlists">Playlists</NavLink>
          </nav>
          <div className="flex-1" />
          <button
            onClick={() => openGrab()}
            className="flex h-7 items-center gap-2 rounded-sm border border-line-strong px-2.5 text-[13px] text-fg transition-colors hover:bg-white/5"
          >
            {activeCount > 0 && (
              <span className="flex items-center gap-1 font-mono text-[11px] text-muted tabular" title={`${activeCount} downloading`}>
                <Loader2 className="size-3 animate-spin" />{activeCount}
              </span>
            )}
            Add <Kbd>⌘K</Kbd>
          </button>
        </div>
      </header>

      <main className="mx-auto max-w-[1440px] px-4 pb-[calc(var(--tabbar)+var(--mini)+2rem)] pt-[env(safe-area-inset-top)] sm:px-8 sm:pb-[calc(var(--mini)+6rem)] sm:pt-0">
        {children}
      </main>

      {/* phone: bottom tab bar, within thumb reach */}
      <nav className="fixed inset-x-0 bottom-0 z-40 flex h-[var(--tabbar)] border-t border-line bg-ink/95 pb-[env(safe-area-inset-bottom)] backdrop-blur sm:hidden">
        <TabLink to="/music" icon={Music2} label="Music" />
        <TabLink to="/videos" icon={Film} label="Videos" />
        <TabLink to="/playlists" icon={ListMusic} label="Playlists" />
        <button onClick={() => openGrab()} className="relative flex flex-1 flex-col items-center justify-center gap-1 text-[10.5px] text-faint active:text-fg">
          {activeCount > 0 ? <Loader2 className="size-5 animate-spin" strokeWidth={1.6} /> : <Plus className="size-5" strokeWidth={1.6} />}
          Add
          {activeCount > 0 && (
            <span className="absolute left-1/2 top-1.5 ml-2 min-w-4 rounded-sm bg-fg px-1 text-center font-mono text-[9.5px] leading-4 text-ink tabular">{activeCount}</span>
          )}
        </button>
      </nav>
    </div>
  );
}
