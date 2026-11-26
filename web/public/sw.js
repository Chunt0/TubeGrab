// TubeGrab service worker: opens instantly and keeps thumbnails on the device.
//  · pages: the cached app shell right away, refreshed in the background
//  · /assets/*: content-hashed, cached for good (current + previous build kept)
//  · versioned thumbnails / covers (?v=…): cached for good, oldest dropped past a cap
//  · /api and /media: always the network (the app caches API answers itself)
const SHELL = "tg-shell-v1";
const IMG = "tg-img-v1";
const IMG_MAX = 4000;

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil((async () => {
  for (const k of await caches.keys()) if (k !== SHELL && k !== IMG) await caches.delete(k);
  await self.clients.claim();
})()));

const assetsIn = (html) => new Set([...html.matchAll(/\/assets\/[^"'\s)]+/g)].map((m) => m[0]));

async function shell(req) {
  const cache = await caches.open(SHELL);
  const cached = await cache.match("/index.html");
  const update = fetch(req).then(async (res) => {
    if (res.ok && (res.headers.get("content-type") || "").includes("text/html")) {
      const html = await res.clone().text();
      const prev = cached ? await cached.clone().text() : "";
      await cache.put("/index.html", res.clone());
      // drop assets neither this build nor the previous one uses
      const keep = new Set([...assetsIn(html), ...assetsIn(prev)]);
      for (const r of await cache.keys()) {
        const p = new URL(r.url).pathname;
        if (p.startsWith("/assets/") && !keep.has(p)) await cache.delete(r);
      }
    }
    return res;
  }).catch(() => null);
  return cached || (await update) || Response.error();
}

async function cacheFirst(name, req, max) {
  const cache = await caches.open(name);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok) {
    await cache.put(req, res.clone());
    if (max && Math.random() < 0.02) {
      const keys = await cache.keys();
      for (const k of keys.slice(0, Math.max(0, keys.length - max))) await cache.delete(k);
    }
  }
  return res;
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  const p = url.pathname;
  if (req.mode === "navigate") return e.respondWith(shell(req));
  if (p.startsWith("/assets/")) return e.respondWith(cacheFirst(SHELL, req));
  if (p.startsWith("/collection-cover/") || ((p.startsWith("/thumb/") || p.startsWith("/playlist-cover/")) && url.searchParams.has("v")))
    return e.respondWith(cacheFirst(IMG, req, IMG_MAX));
});
