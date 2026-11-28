import { CheckCheck, Film, Heart, HeartOff, ListPlus, Music, RotateCcw } from "lucide-react";
import { resumable, type LibraryFile } from "../api";
import { useFavorite, useMark, useStore } from "../store";
import { BrokenHeart } from "./Heart";
import { cx } from "../lib/format";
import { Menu, MenuItem } from "./ui";

/** Per-track / per-video "⋯" (hover on desktop, always there on touch screens). */
export function ItemMenu({ file, className }: { file: LibraryFile; className?: string }) {
  const { openAddToPlaylist, progress, setWatched, move } = useStore();
  const toMusic = file.section !== "Music";
  const watched = !!progress[file.name]?.watched;
  const [fav, toggleFav] = useFavorite("song", file.name, file);
  const [disliked, toggleDislike] = useMark("dislike", "song", file.name, file);
  return (
    <Menu className={cx("opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100 [@media(hover:none)]:opacity-100", className)}>
      {(close) => (
        <>
          {file.section === "Music" && (
            <MenuItem icon={fav ? HeartOff : Heart} onClick={() => { close(); toggleFav(); }}>
              {fav ? "Remove from favorites" : "Add to favorites"}
            </MenuItem>
          )}
          {file.section === "Music" && (
            <MenuItem icon={BrokenHeart} onClick={() => { close(); toggleDislike(); }}>
              {disliked ? "Remove dislike" : "Dislike"}
            </MenuItem>
          )}
          <MenuItem icon={ListPlus} onClick={() => { close(); openAddToPlaylist([file.name], file.title); }}>Add to playlist…</MenuItem>
          {resumable(file) && (
            <MenuItem icon={watched ? RotateCcw : CheckCheck} onClick={() => { close(); setWatched(file, !watched).catch(() => {}); }}>
              {watched ? "Mark as unwatched" : "Mark as watched"}
            </MenuItem>
          )}
          <MenuItem icon={toMusic ? Music : Film} onClick={() => { close(); move({ name: file.name }, toMusic ? "Music" : "Videos"); }}>
            {toMusic ? "Move to Music" : "Move to Videos"}
          </MenuItem>
        </>
      )}
    </Menu>
  );
}
