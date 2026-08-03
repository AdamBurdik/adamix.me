const POLL_INTERVAL_MS = 60_000;
const root = document.getElementById("now-playing");
const statusText = document.getElementById("status");
const cover = document.getElementById("cover");
const avatar = document.getElementById("avatar");
const titleEl = document.getElementById("title");
const artistEl = document.getElementById("artist");
const albumEl = document.getElementById("album");

const PLACEHOLDER =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120"><rect width="120" height="120" fill="#2a2a2a"/><text x="60" y="66" font-size="28" text-anchor="middle" fill="#505050">♪</text></svg>`
  );

let currentTrack = null;

function render(data) {
  if (!data || !data.playing_now) {
    currentTrack = null;
    root.classList.remove("is-playing");
    return;
  }

  root.classList.add("is-playing");

  const trackKey = `${data.title}::${data.artist}`;
  currentTrack = { ...data, key: trackKey };

  statusText.textContent = "Listening to";
  titleEl.textContent = data.title;
  artistEl.textContent = data.artist;
  albumEl.textContent = data.album;

  if (data.cover_url) {
    cover.src = data.cover_url;
    cover.classList.remove("cover--placeholder");
  } else {
    cover.src = PLACEHOLDER;
    cover.classList.add("cover--placeholder");
  }

  avatar.src = data.avatar_url || PLACEHOLDER;
}

async function refresh() {
  try {
    const res = await fetch("/api/now-playing");
    render(await res.json());
  } catch {
    render(null);
  }
}

refresh();
setInterval(refresh, POLL_INTERVAL_MS);

const ODESLI_API = "https://api.song.link/v1-alpha.1/links";

root.addEventListener("click", async () => {
  if (!currentTrack || !currentTrack.track_url) return;

  const win = window.open("", "_blank");
  try {
    const res = await fetch(
      `${ODESLI_API}?url=${encodeURIComponent(currentTrack.track_url)}&type=song`
    );
    const data = await res.json();
    win.location.href = data.pageUrl || currentTrack.track_url;
  } catch {
    win.location.href = currentTrack.track_url;
  }
});
