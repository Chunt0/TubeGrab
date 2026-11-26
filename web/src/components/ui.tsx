import { useEffect, useRef, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { MoreHorizontal, X } from "lucide-react";
import { cx } from "../lib/format";

type Variant = "primary" | "outline" | "ghost" | "danger";
export function Button({ variant = "outline", size = "md", className, ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md" }) {
  return (
    <button
      {...rest}
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-sm font-medium transition-colors select-none disabled:opacity-40",
        size === "sm" ? "h-7 px-2.5 text-xs" : "h-8 px-3 text-[13px]",
        variant === "primary" && "bg-fg text-ink hover:bg-white",
        variant === "outline" && "border border-line-strong text-fg hover:bg-white/5",
        variant === "ghost" && "text-muted hover:text-fg",
        variant === "danger" && "border border-line-strong text-muted hover:border-bad/50 hover:text-bad",
        className,
      )}
    />
  );
}

export function IconButton({ label, className, children, ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button
      {...rest}
      aria-label={label}
      title={label}
      className={cx("grid size-8 place-items-center rounded-sm text-faint transition-colors hover:text-fg", className)}
    >
      {children}
    </button>
  );
}

export function Modal({ open, onClose, children, className }:
  { open: boolean; onClose: () => void; children: ReactNode; className?: string }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { removeEventListener("keydown", onKey); document.body.style.overflow = prev; };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[60] flex items-end justify-center animate-fade sm:items-start sm:p-4 sm:pt-[14vh]">
      <div className="absolute inset-0 bg-black/75" onClick={onClose} />
      {/* phone: bottom sheet above the keyboard-safe area · larger: centred card */}
      <div role="dialog" aria-modal="true"
        className={cx("relative max-h-[90dvh] w-full max-w-xl overflow-y-auto rounded-t-md border-t border-line-strong bg-surface pb-[env(safe-area-inset-bottom)] sm:rounded-sm sm:border sm:pb-0", className)}>
        <IconButton label="Close" onClick={onClose} className="absolute right-2 top-2">
          <X className="size-4" />
        </IconButton>
        {children}
      </div>
    </div>
  );
}

export function Confirm({ open, title, body, confirmLabel = "Delete", onConfirm, onClose }: {
  open: boolean; title: string; body?: ReactNode; confirmLabel?: string;
  onConfirm: () => void; onClose: () => void;
}) {
  return (
    <Modal open={open} onClose={onClose} className="max-w-sm">
      <div className="p-5">
        <h2 className="pr-8 text-[15px] font-medium">{title}</h2>
        {body && <div className="mt-2 text-[13px] leading-relaxed text-muted">{body}</div>}
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="danger" autoFocus onClick={() => { onConfirm(); onClose(); }}
            className="!border-bad/40 !text-bad hover:!bg-bad/10">{confirmLabel}</Button>
        </div>
      </div>
    </Modal>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="font-mono text-[10.5px] text-faint">{children}</kbd>;
}

/** Small square-ish switch */
export function Switch({ on }: { on: boolean }) {
  return (
    <span className={cx("relative inline-block h-3.5 w-6 rounded-sm border transition-colors",
      on ? "border-fg bg-fg" : "border-line-strong bg-transparent")}>
      <span className={cx("absolute top-[1px] size-2.5 rounded-[1px] transition-all",
        on ? "left-[11px] bg-ink" : "left-[1px] bg-faint")} />
    </span>
  );
}

/** Text tab used for segmented choices */
export function Tab({ active, children, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { active: boolean }) {
  return (
    <button
      type="button"
      {...rest}
      className={cx(
        "relative h-8 px-0.5 text-[13px] transition-colors",
        active ? "text-fg after:absolute after:inset-x-0 after:-bottom-px after:h-px after:bg-fg" : "text-muted hover:text-fg",
      )}
    >
      {children}
    </button>
  );
}

/** "⋯" overflow menu: keeps rarely used / destructive actions out of reach of stray taps. */
export function Menu({ label = "More actions", children, className }:
  { label?: string; children: (close: () => void) => ReactNode; className?: string }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    addEventListener("pointerdown", away);
    addEventListener("keydown", esc);
    return () => { removeEventListener("pointerdown", away); removeEventListener("keydown", esc); };
  }, [open]);
  return (
    <div ref={root} className={cx("relative", className)}>
      <IconButton label={label} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}
        className={cx("border border-transparent", open && "border-line-strong text-fg")}>
        <MoreHorizontal className="size-4" />
      </IconButton>
      {open && (
        <div role="menu" className="absolute right-0 top-full z-30 mt-1 min-w-48 rounded-sm border border-line-strong bg-raised py-1 shadow-xl shadow-black/50 animate-fade">
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  );
}

export function MenuItem({ icon: Icon, children, danger, href, download, target, onClick }: {
  icon: typeof MoreHorizontal; children: ReactNode; danger?: boolean;
  href?: string; download?: string; target?: string; onClick?: () => void;
}) {
  const cls = cx("flex w-full items-center gap-2.5 px-3 py-2 text-left text-[13px] transition-colors hover:bg-hover",
    danger ? "text-bad" : "text-fg/90");
  const body = <><Icon className="size-3.5 shrink-0 opacity-80" />{children}</>;
  return href
    ? <a role="menuitem" href={href} download={download} target={target} rel={target ? "noreferrer" : undefined} onClick={onClick} className={cls}>{body}</a>
    : <button role="menuitem" type="button" onClick={onClick} className={cls}>{body}</button>;
}

export const MenuDivider = () => <div className="my-1 border-t border-line" />;
