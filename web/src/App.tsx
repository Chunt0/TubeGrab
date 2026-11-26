import { useEffect } from "react";
import { useRouter } from "./router";
import { Shell } from "./components/Shell";
import { GrabDialog } from "./components/GrabDialog";
import { Toasts } from "./components/Toasts";
import { Music } from "./pages/Music";
import { ArtistPage } from "./pages/Artist";
import { Videos } from "./pages/Videos";
import { Watch } from "./pages/Watch";
import { Downloads } from "./pages/Downloads";
import { CollectionPage } from "./pages/Collection";
import { Playlists } from "./pages/Playlists";
import { PlaylistPage } from "./pages/PlaylistPage";
import { AddToPlaylist } from "./components/AddToPlaylist";

/** "/" → Music, straight away. */
function Home() {
  const { navigate } = useRouter();
  useEffect(() => { navigate("/music", true); }, [navigate]);
  return null;
}

export function App() {
  const { path } = useRouter();
  const m = (re: RegExp) => path.match(re);
  let page;
  let r;
  if ((r = m(/^\/watch\/(.+)$/))) page = <Watch name={decodeURIComponent(r[1])} />;
  else if ((r = m(/^\/c\/(.+)$/))) page = <CollectionPage key={r[1]} name={decodeURIComponent(r[1])} />;
  else if ((r = m(/^\/music\/artist\/(.+)$/))) page = <ArtistPage name={decodeURIComponent(r[1])} />;
  else if (path.startsWith("/music")) page = <Music />;
  else if (path.startsWith("/videos")) page = <Videos />;
  else if (path.startsWith("/downloads")) page = <Downloads />;
  else if ((r = m(/^\/p\/([^/]+)$/))) page = <PlaylistPage id={decodeURIComponent(r[1])} />;
  else if (path.startsWith("/playlists")) page = <Playlists />;
  else page = <Home />;

  return (
    <>
      <Shell>{page}</Shell>
      <GrabDialog />
      <AddToPlaylist />
      <Toasts />
    </>
  );
}
