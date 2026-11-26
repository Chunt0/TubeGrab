import { useState } from "react";
import { Trash2 } from "lucide-react";
import { isActive, urls, type LibraryFile } from "../api";
import { usePaged } from "../lib/cache";
import { useStore } from "../store";
import { Link, watchPath } from "../router";
import { GrabForm } from "../components/GrabForm";
import { JobCard } from "../components/JobCard";
import { Confirm, IconButton } from "../components/ui";
import { ago, bytes, duration } from "../lib/format";

function Section({ title, count, children }: { title: string; count?: number; children: React.ReactNode }) {
  return (
    <section className="mt-12">
      <h2 className="flex items-baseline gap-2 border-b border-line pb-2 text-[13px] font-medium">
        {title}{count != null && <span className="font-mono text-[11px] font-normal text-faint tabular">{count}</span>}
      </h2>
      {children}
    </section>
  );
}

export function Downloads() {
  const { jobs, library, remove } = useStore();
  const files = usePaged<LibraryFile>(urls.files({ sort: "new" }), 100);
  const dir = library?.dir;
  const [pending, setPending] = useState<LibraryFile | null>(null);
  const active = jobs.filter(isActive);
  const done = jobs.filter((j) => !isActive(j));

  return (
    <div className="mx-auto max-w-4xl pt-10">
      <h1 className="text-[15px] font-medium">Add</h1>
      <div className="mt-4"><GrabForm /></div>

      {active.length > 0 && (
        <Section title="In progress" count={active.length}>{active.map((j) => <JobCard key={j.id} job={j} />)}</Section>
      )}
      {done.length > 0 && (
        <Section title="Finished" count={done.length}>{done.map((j) => <JobCard key={j.id} job={j} />)}</Section>
      )}

      <Section title="Files" count={files.total}>
        {files.items.length ? (
          <div>
            {files.items.map((f) => (
              <div key={f.name} className="group flex items-center gap-4 border-b border-line py-2">
                <Link to={watchPath(f.name)} className="min-w-0 flex-1">
                  <div className="truncate text-[13px] text-fg/90 group-hover:text-white">{f.title}</div>
                  <div className="truncate font-mono text-[11px] text-faint">{f.name}</div>
                </Link>
                <span className="hidden w-14 text-right font-mono text-[11px] text-muted tabular sm:block">{duration(f.duration)}</span>
                <span className="hidden w-16 text-right font-mono text-[11px] text-muted tabular sm:block">{bytes(f.size)}</span>
                <span className="hidden w-24 text-right text-[11px] text-muted md:block">{ago(f.mtime)}</span>
                <IconButton label="Delete" onClick={() => setPending(f)} className="opacity-0 hover:!text-bad group-hover:opacity-100 focus:opacity-100 [@media(hover:none)]:opacity-100">
                  <Trash2 className="size-3.5" />
                </IconButton>
              </div>
            ))}
            {files.sentinel}
          </div>
        ) : (
          files.loaded && <p className="py-8 text-[13px] text-muted">No files.</p>
        )}
        {dir && <p className="mt-3 font-mono text-[11px] text-faint">{dir}</p>}
      </Section>

      <Confirm open={!!pending} onClose={() => setPending(null)} onConfirm={() => pending && remove(pending)}
        title="Delete this file?" body={pending && <>“{pending.title}” will be permanently removed.</>} />
    </div>
  );
}
