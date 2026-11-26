import { useEffect, useState } from "react";

/** Mirror the media element's state into React. */
export function useMediaState(el: HTMLVideoElement) {
  const read = () => ({
    paused: el.paused, time: el.currentTime, dur: isFinite(el.duration) ? el.duration : 0,
    muted: el.muted, volume: el.volume, waiting: false,
  });
  const [s, setS] = useState(read);
  useEffect(() => {
    const sync = () => setS((p) => ({ ...read(), waiting: p.waiting && el.readyState < 3 }));
    const wait = () => setS((p) => ({ ...p, waiting: true }));
    const evs = ["play", "pause", "timeupdate", "durationchange", "loadedmetadata", "volumechange", "seeked", "emptied", "playing", "canplay"];
    evs.forEach((e) => el.addEventListener(e, sync));
    el.addEventListener("waiting", wait);
    sync();
    return () => { evs.forEach((e) => el.removeEventListener(e, sync)); el.removeEventListener("waiting", wait); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [el]);
  return s;
}

