import { useEffect, useRef, useState } from "react";
import { Heart } from "../components/Heart";
import { ArrowLeft, CheckCheck, Download, Film, ListPlus, Mic2, Music, RotateCcw, Trash2 } from "lucide-react";
import { useStore } from "../store";
import { Link, artistPath, collectionPath, playlistPath, useRouter, watchPath } from "../router";
import { HttpError, api, mediaUrl, resumable, urls, type Chapter, type LibraryFile, type Segment, type WatchData } from "../api";
import { useQuery } from "../lib/cache";
import { usePlayer } from "../player";
import { ago, bytes, cleanChannel, cx, duration, resLabel, uploadDate } from "../lib/format";
import { Thumb } from "../components/Thumb";
import { Confirm, Menu, MenuDivider, MenuItem, Switch } from "../components/ui";
import { PlayerStage } from "../components/PlayerStage";
import { chapterAt, segmentAt, useTranscript } from "../lib/transcript";

/** Current playback time of the shared player (updates ~4×/s while playing). */
function useCurrentTime(el: HTMLVideoElement) {
  const [t, setT] = useState(el.currentTime);
  useEffect(() => {
    const on = () => setT(el.currentTime);
    el.addEventListener("timeupdate", on);
    el.addEventListener("seeked", on);
    return () => { el.removeEventListener("timeupdate", on); el.removeEventListener("seeked", on); };
  }, [el]);
  return t;
}

const jump = (el: HTMLVideoElement, t: number) => { el.currentTime = t; el.play().catch(() => {}); };

function ChapterList({ chapters, el }: { chapters: Chapter[]; el: HTMLVideoElement }) {
  const now = chapterAt(chapters, useCurrentTime(el));
  return (
    <div className="mt-1">
      {chapters.map((c, i) => (
        <button key={c.start} type="button" onClick={() => jump(el, c.start)}
          className={cx("flex w-full items-baseline gap-3 border-b border-line py-2 text-left text-[13px] hover:text-white", i === now ? "text-fg" : "text-fg/65")}>
          <span className="w-12 shrink-0 font-mono text-[11px] text-faint tabular">{duration(c.start) || "0:00"}</span>
          <span className="min-w-0 flex-1">{c.title}</span>
          {i === now && <span className="size-1.5 shrink-0 self-center bg-fg" />}
        </button>
      ))}
    </div>
  );
}

function TranscriptView({ segs, el, lyrics }: { segs: Segment[]; el: HTMLVideoElement; lyrics?: boolean }) {
  const now = segmentAt(segs, useCurrentTime(el));
  const box = useRef<HTMLDivElement>(null);
  const userScroll = useRef(0);
  // follow along, unless you scrolled in the last few seconds
  useEffect(() => {
    const b = box.current;
    const row = b?.querySelector<HTMLElement>(`[data-i="${now}"]`);
    if (!b || !row || Date.now() - userScroll.current < 4000) return;
    const top = row.offsetTop - b.offsetTop;
    if (top < b.scrollTop + 40 || top > b.scrollTop + b.clientHeight - 80) b.scrollTo({ top: top - b.clientHeight / 3, behavior: "smooth" });
  }, [now]);
  return (
    <div ref={box} onWheel={() => { userScroll.current = Date.now(); }} onTouchMove={() => { userScroll.current = Date.now(); }}
      className="mt-1 max-h-[70vh] overflow-y-auto pr-1">
      {segs.map((s, i) => (
        <button key={i} data-i={i} type="button" onClick={() => jump(el, s.start)}
          className={cx("flex w-full gap-3 text-left leading-relaxed transition-colors hover:text-white",
            lyrics ? "py-1.5 text-[16px] font-medium" : "py-1 text-[13px]",
            i === now ? "text-fg" : lyrics ? "text-fg/35" : "text-fg/55")}>
          {!lyrics && <span className="w-10 shrink-0 pt-px font-mono text-[10.5px] text-faint tabular">{duration(s.start) || "0:00"}</span>}
          <span className="min-w-0 flex-1">{s.text}</span>
        </button>
      ))}
    </div>
  );
}


function UpNextItem({ file, active, className, list }: { file: LibraryFile; active?: boolean; className?: string; list?: string | null }) {
  const square = file.section === "Music";
  return (
    <Link to={watchPath(file.name, list)} className={cx("group flex items-center gap-3 py-2", className)}>
      <div className={cx("relative shrink-0", square ? "w-12" : "w-28 sm:w-36")}>
        <Thumb file={file} className={cx("rounded-sm", square ? "aspect-square" : "aspect-video")} />
        {!square && file.duration != null && (
          <span className="absolute bottom-1 right-1 bg-black/80 px-1 font-mono text-[10px] text-white tabular">
            {duration(file.duration)}
          </span>
        )}
      </div>
      <div className="min-w-0">
        <div className={cx("line-clamp-2 text-[13px] leading-snug group-hover:text-white", active ? "text-fg" : "text-fg/80")}>
          {file.title}
        </div>
        <div className="mt-1 text-[11.5px] text-muted">{(file.section === "Music" ? file.artist : cleanChannel(file.uploader)) || bytes(file.size)}</div>
        {active && <div className="mt-1 text-[11px] text-faint">Next</div>}
      </div>
      {square && file.duration != null && (
        <span className="ml-auto shrink-0 font-mono text-[11px] text-faint tabular">{duration(file.duration)}</span>
      )}
    </Link>
  );
}

function Meta({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-4 py-1.5">
      <dt className="w-20 shrink-0 text-muted">{label}</dt>
      <dd className="min-w-0 break-words text-fg/85">{children}</dd>
    </div>
  );
}

export function Watch({ name }: { name: string }) {
  const { remove, openAddToPlaylist, progress, setWatched, refresh, toast, move } = useStore();
  const { navigate, search } = useRouter();
  const player = usePlayer();
  const { autoplay, setAutoplay } = player;
  const [confirm, setConfirm] = useState(false);
  const [allNext, setAllNext] = useState(false);

  // ?list=<id>: playing through one of your playlists
  const listId = new URLSearchParams(search).get("list");
  const { data, error } = useQuery<WatchData>(urls.watch(name, listId), { keep: true });
  // while the next file loads, the previous page stays (so the player isn't torn down between tracks)
  const view = data;
  const file = view?.file;
  const coll = view?.collection ?? undefined;
  const pl = view?.playlist ?? null;
  const { setList } = player;
  useEffect(() => { if (view) setList(pl ? pl.id : null); }, [view, pl?.id, setList]); // eslint-disable-line react-hooks/exhaustive-deps
  const music = file?.section === "Music";
  // Queue: rest of this playlist / collection, then the newest from the same section.
  const upNext = view?.next ?? [];
  const next = upNext[0] ?? null;

  const { open } = player;
  const startAt = Number(new URLSearchParams(search).get("t")) || null;   // ?t=123 (from transcript search)
  const applied = useRef("");   // apply ?t= once per link, not on every library refresh
  useEffect(() => {
    if (!file) return;
    const key = `${file.name}@${startAt}`;
    open(file, applied.current === key ? null : startAt);
    applied.current = key;
  }, [file, open, startAt]);
  const segs = useTranscript(file);
  const [tab, setTab] = useState<"next" | "chapters" | "transcript" | "lyrics">("next");
  const [lyrics, setLyrics] = useState<{ synced: Segment[] | null; plain: string | null } | null>(null);
  useEffect(() => {
    setLyrics(null);
    if (!file?.lyrics) return;
    let live = true;
    api.lyrics(file.name).then((l) => { if (live) setLyrics(l); }).catch(() => {});
    return () => { live = false; };
  }, [file?.name, file?.lyrics]);
  const findLyrics = async () => {
    if (!file) return;
    try {
      const found = await api.findLyrics(file.name);
      refresh();
      toast(found ? { kind: "ok", title: "Lyrics found" } : { kind: "info", title: "No lyrics found" });
      if (found) setTab("lyrics");
    } catch (e) {
      toast({ kind: "bad", title: "Couldn't look up lyrics", body: e instanceof Error ? e.message : String(e) });
    }
  };
  useEffect(() => setTab("next"), [name]);
  useEffect(() => { if (error instanceof HttpError && error.status === 404) navigate("/", true); }, [error, navigate]);
  useEffect(() => { document.title = file ? file.title : "Library"; return () => { document.title = "Library"; }; }, [file]);
  useEffect(() => { setAllNext(false); }, [name]);

  if (!file) return null;

  const doDelete = async () => {
    await remove(file);
    navigate(next ? watchPath(next.name, pl?.id) : pl ? playlistPath(pl.id) : coll ? collectionPath(coll.key) : "/", true);
  };

  return (
    <div className="pt-3 sm:pt-6">
      <Link to={pl ? playlistPath(pl.id) : coll ? collectionPath(coll.key) : music ? "/music" : "/videos"} className="inline-flex items-center gap-1.5 text-xs text-muted hover:text-fg">
        <ArrowLeft className="size-3.5" /> {pl ? pl.title : coll ? (coll.kind === "singles" ? `${coll.artist} · Singles` : coll.title) : music ? "Music" : "Videos"}
      </Link>

      <div className="mt-4 grid gap-10 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0">
          <PlayerStage music={music} />

          <div className="mt-5 flex flex-wrap items-start gap-4">
            <div className="min-w-0 flex-1">
              <h1 className="text-lg font-medium leading-snug tracking-tight">{file.title}</h1>
              <p className="mt-1 text-[13px] text-muted">
                {music ? (
                  <>
                    {file.artist && <Link to={artistPath(coll?.artist ?? file.artist)} className="hover:text-fg hover:underline">{file.artist}</Link>}
                    {coll?.kind === "album" && <> · <Link to={collectionPath(coll.key)} className="hover:text-fg hover:underline">{coll.title}</Link></>}
                    {file.year && <> · {file.year}</>}
                  </>
                ) : (
                  <>
                    {cleanChannel(file.uploader) || coll?.title}
                    {file.upload_date && <> · {uploadDate(file.upload_date)}</>}
                  </>
                )}
              </p>
            </div>
            {music && <Heart kind="song" id={file.name} file={file} className="-mt-1" />}
            <Menu className="-mr-2 -mt-1">
              {(close) => (
                <>
                  <MenuItem icon={ListPlus} onClick={() => { close(); openAddToPlaylist([file.name], file.title); }}>Add to playlist…</MenuItem>
                  {music && <MenuItem icon={Mic2} onClick={() => { close(); findLyrics(); }}>{file.lyrics ? "Look up lyrics again" : "Find lyrics"}</MenuItem>}
                  {resumable(file) && (progress[file.name]?.watched
                    ? <MenuItem icon={RotateCcw} onClick={() => { close(); setWatched(file, false).catch(() => {}); }}>Mark as unwatched</MenuItem>
                    : <MenuItem icon={CheckCheck} onClick={() => { close(); setWatched(file, true).catch(() => {}); }}>Mark as watched</MenuItem>)}
                  <MenuItem icon={music ? Film : Music} onClick={async () => {
                    close();
                    const to = await move({ name: file.name }, music ? "Videos" : "Music");
                    if (to) navigate(watchPath(to, listId), true);
                  }}>
                    {music ? "Move to Videos" : "Move to Music"}
                  </MenuItem>
                  <MenuItem icon={Download} href={mediaUrl(file.name)} download={file.name.split("/").pop()} onClick={close}>
                    Save to device
                  </MenuItem>
                  <MenuDivider />
                  <MenuItem icon={Trash2} danger onClick={() => { close(); setConfirm(true); }}>Delete file…</MenuItem>
                </>
              )}
            </Menu>
          </div>

          <dl className="mt-5 border-t border-line pt-3 text-[12.5px]">
            {coll && (
              <Meta label={{ album: "Album", singles: "Singles", playlist: "Playlist", channel: "Channel", folder: "Folder" }[coll.kind]}>
                <Link to={collectionPath(coll.key)} className="hover:text-white hover:underline">{coll.kind === "singles" ? coll.artist : coll.title}</Link>
                {(coll.kind === "playlist" || coll.kind === "album") && file.index != null && <span className="text-muted"> · #{file.index}</span>}
              </Meta>
            )}
            <Meta label="Details">
              <span className="font-mono text-xs tabular">
                {[duration(file.duration), resLabel(file.height), bytes(file.size), file.name.split(".").pop()?.toUpperCase()].filter(Boolean).join(" · ")}
              </span>
            </Meta>
            <Meta label="Added">{ago(file.mtime)}</Meta>
            {file.url && <Meta label="Source"><a href={file.url} target="_blank" rel="noreferrer" className="hover:text-white hover:underline">{file.url.replace(/^https?:\/\/(www\.)?/, "")}</a></Meta>}
            <Meta label="File"><span className="font-mono text-[11.5px] text-muted">{file.name}</span></Meta>
          </dl>
        </div>

        <aside className="min-w-0">
          <div className="flex items-center gap-4 border-b border-line">
            <div role="tablist" className="flex min-w-0 flex-1 items-center gap-4">
              {([
                ["next", pl ? <>{pl.title} <span className="font-mono text-[11px] font-normal text-faint tabular">{pl.position + 1}/{pl.total}</span></> : "Up next"],
                ...(file.chapters ? [["chapters", "Chapters"]] : []),
                ...(segs?.length ? [["transcript", "Transcript"]] : []),
                ...(file.lyrics ? [["lyrics", "Lyrics"]] : []),
              ] as [typeof tab, React.ReactNode][]).map(([id, label]) => (
                <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}
                  className={cx("relative min-w-0 truncate py-2 text-[13px] font-medium transition-colors",
                    tab === id ? "text-fg after:absolute after:inset-x-0 after:-bottom-px after:h-px after:bg-fg" : "text-muted hover:text-fg")}>
                  {label}
                </button>
              ))}
            </div>
            {tab === "next" && (
              <button role="switch" aria-checked={autoplay} onClick={() => setAutoplay(!autoplay)}
                className="flex shrink-0 items-center gap-2 text-xs text-muted hover:text-fg">
                Autoplay <Switch on={autoplay} />
              </button>
            )}
          </div>
          {tab === "chapters" && file.chapters ? <ChapterList chapters={file.chapters} el={player.media} />
          : tab === "transcript" && segs?.length ? <TranscriptView segs={segs} el={player.media} />
          : tab === "lyrics" && file.lyrics ? (
            !lyrics ? <div className="mt-4 h-40 animate-pulse bg-surface" />
            : lyrics.synced ? <TranscriptView segs={lyrics.synced} el={player.media} lyrics />
            : <p className="mt-3 max-h-[70vh] overflow-y-auto whitespace-pre-line text-[14px] leading-relaxed text-fg/80">{lyrics.plain}</p>
          )
          : upNext.length
            ? (
              <div className="mt-1">
                {upNext.map((f, i) => (
                  <UpNextItem key={f.name} file={f} list={pl?.id} active={autoplay && f.name === next?.name}
                    className={!allNext && i >= 6 ? "max-sm:hidden" : undefined} />
                ))}
                {!allNext && upNext.length > 6 && (
                  <button onClick={() => setAllNext(true)} className="mt-1 w-full border-t border-line py-3 text-xs text-muted sm:hidden">
                    Show {upNext.length - 6} more
                  </button>
                )}
              </div>
            )
            : <p className="py-8 text-[13px] text-muted">Nothing else yet.</p>}
        </aside>
      </div>

      <Confirm open={confirm} onClose={() => setConfirm(false)} onConfirm={doDelete} title="Delete this file?"
        body={<>“{file.title}” will be permanently removed.</>} />
    </div>
  );
}
