import { useEffect } from "react";
import { useStore } from "../store";
import { ChevronRight } from "lucide-react";
import { isActive } from "../api";
import { Link } from "../router";
import { Modal } from "./ui";
import { GrabForm } from "./GrabForm";

const isUrl = (s: string) => /^https?:\/\/\S+$/i.test(s.trim());
const typing = (el: EventTarget | null) =>
  el instanceof HTMLElement && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));

/** Global quick-add: ⌘K / "/" opens it, pasting a link anywhere prefills it. */
export function GrabDialog() {
  const { grabOpen, grabPrefill, openGrab, closeGrab, jobs } = useStore();
  const active = jobs.filter(isActive);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && !typing(e.target))) {
        e.preventDefault();
        openGrab();
      }
    };
    const onPaste = (e: ClipboardEvent) => {
      if (typing(e.target) || grabOpen) return;
      const t = e.clipboardData?.getData("text") ?? "";
      if (isUrl(t)) { e.preventDefault(); openGrab(t.trim()); }
    };
    addEventListener("keydown", onKey);
    addEventListener("paste", onPaste);
    return () => { removeEventListener("keydown", onKey); removeEventListener("paste", onPaste); };
  }, [openGrab, grabOpen]);

  return (
    <Modal open={grabOpen} onClose={closeGrab} className="max-w-2xl">
      <div className="p-5">
        <h2 className="text-[13px] text-muted">Add video or playlist</h2>
        <div className="mt-4"><GrabForm prefill={grabPrefill} autoFocus onSubmitted={closeGrab} /></div>
        <div className="mt-5 border-t border-line pt-3">
          {active.map((j) => (
            <div key={j.id} className="mb-2.5">
              <div className="flex items-baseline gap-3 text-[12.5px]">
                <span className="min-w-0 flex-1 truncate text-fg/85">{j.title || j.url}</span>
                <span className="shrink-0 font-mono text-[11px] text-muted tabular">
                  {j.playlist_count && j.playlist_count > 1 ? `${j.playlist_index ?? "?"}/${j.playlist_count} · ` : ""}
                  {j.status === "queued" ? "queued" : `${Math.round(j.percent ?? 0)}%`}
                </span>
              </div>
              <div className="mt-1 h-px bg-line"><div className="h-px bg-fg transition-[width]" style={{ width: `${j.percent ?? 0}%` }} /></div>
            </div>
          ))}
          <Link to="/downloads" onClick={closeGrab} className="flex items-center justify-between text-[12.5px] text-muted hover:text-fg">
            Downloads <ChevronRight className="size-3.5" />
          </Link>
        </div>
      </div>
    </Modal>
  );
}
