import { X } from "lucide-react";
import { useStore } from "../store";
import { cx } from "../lib/format";

export function Toasts() {
  const { toasts, dismiss } = useStore();
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-[calc(var(--tabbar)+var(--mini))] z-[70] flex flex-col items-center gap-2 p-4 sm:bottom-[var(--mini)] sm:items-end sm:p-6">
      {toasts.map((t) => (
        <div key={t.id}
          className="pointer-events-auto flex w-full max-w-xs items-start gap-3 rounded-sm border border-line-strong bg-surface py-2.5 pl-3 pr-1.5 animate-fade">
          <span className={cx("mt-1.5 size-1.5 shrink-0 rounded-full",
            t.kind === "ok" ? "bg-ok" : t.kind === "bad" ? "bg-bad" : "bg-muted")} />
          <div className="min-w-0 flex-1">
            <div className="text-[13px]">{t.title}</div>
            {t.body && <div className="mt-0.5 line-clamp-2 break-all text-xs text-muted">{t.body}</div>}
          </div>
          <button onClick={() => dismiss(t.id)} className="p-1 text-faint hover:text-fg" aria-label="Dismiss">
            <X className="size-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
}
