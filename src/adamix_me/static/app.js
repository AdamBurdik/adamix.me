const $ = (selector, root = document) => root.querySelector(selector);

const NOW_PLAYING_INTERVAL = 30_000;
const TOP_TRACKS_INTERVAL = 10 * 60_000;
const ACTIVITY_INTERVAL = 5 * 60_000;

async function getJSON(url) {
  const response = await fetch(url, {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`${url} responded ${response.status}`);
  }
  return response.json();
}

/* ----- now playing ----- */

let coverToken = 0;

function renderCover(data, playing) {
  const cover = $("#np-cover");
  const fallback = $("#np-cover-fallback");
  if (!cover || !fallback) return;

  const token = ++coverToken;
  const url = playing && data ? data.cover_url : "";

  if (!url) {
    cover.onload = null;
    cover.onerror = null;
    cover.hidden = true;
    cover.removeAttribute("src");
    cover.alt = "";
    fallback.hidden = false;
    return;
  }

  const album = data.album && data.album !== "Unknown album" ? data.album : "";
  const label = album || data.title || "Album";
  const alt = `${label} cover`;

  if (
    cover.getAttribute("src") === url &&
    cover.complete &&
    cover.naturalWidth > 0
  ) {
    cover.alt = alt;
    cover.hidden = false;
    fallback.hidden = true;
    return;
  }

  cover.hidden = true;
  fallback.hidden = false;
  cover.alt = alt;

  cover.onload = () => {
    if (token !== coverToken) return;
    cover.hidden = false;
    fallback.hidden = true;
  };

  cover.onerror = () => {
    if (token !== coverToken) return;
    cover.hidden = true;
    fallback.hidden = false;
  };

  cover.src = url;
}

function renderNowPlaying(data) {
  const info = $("#now-info");
  const title = $("#np-title");
  const artist = $("#np-artist");
  const album = $("#np-album");
  if (!info || !title || !artist || !album) return;

  const playing = Boolean(data && data.playing_now);
  info.classList.toggle("is-playing", playing);
  renderCover(data, playing);

  if (!playing) {
    title.textContent = "Nothing playing";
    artist.textContent = "silence is golden";
    album.textContent = "";
    return;
  }

  title.textContent = data.title || "Unknown track";
  artist.textContent = data.artist || "Unknown artist";
  album.textContent = data.album && data.album !== "Unknown album" ? data.album : "";
}

async function loadNowPlaying() {
  try {
    renderNowPlaying(await getJSON("/api/now-playing"));
  } catch {
    renderNowPlaying({ playing_now: false });
  }
}

/* ----- top tracks ----- */

function renderTopTracks(tracks) {
  const list = $("#top-tracks");
  if (!list) return;
  list.replaceChildren();

  if (!Array.isArray(tracks) || tracks.length === 0) {
    const item = document.createElement("li");
    item.className = "empty";
    item.textContent = "no listening data";
    list.append(item);
    return;
  }

  tracks.forEach((track, index) => {
    const item = document.createElement("li");

    const rank = document.createElement("span");
    rank.className = "rank";
    rank.textContent = `${index + 1}.`;

    const meta = document.createElement("span");
    meta.className = "meta";

    const title = document.createElement("span");
    title.className = "track";
    title.textContent = track.title || "Unknown track";

    const artist = document.createElement("span");
    artist.className = "artist";
    artist.textContent = track.artist ? `– ${track.artist}` : "";

    meta.append(title, artist);
    item.append(rank, meta);
    list.append(item);
  });
}

async function loadTopTracks() {
  try {
    const data = await getJSON("/api/top-tracks");
    renderTopTracks(data.tracks);
  } catch {
    renderTopTracks([]);
  }
}

/* ----- activity ----- */

function timeLabel(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--:--";
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function renderActivity(items) {
  const list = $("#activity");
  if (!list) return;
  list.replaceChildren();

  if (!Array.isArray(items) || items.length === 0) {
    const item = document.createElement("li");
    item.className = "empty";
    item.textContent = "nothing to report";
    list.append(item);
    return;
  }

  items.forEach((entry) => {
    const item = document.createElement("li");

    const time = document.createElement("time");
    time.dateTime = entry.at || "";
    time.textContent = timeLabel(entry.at);

    const message = document.createElement("span");
    message.className = "msg";
    message.textContent = entry.text || "";

    if (entry.url) {
      const link = document.createElement("a");
      link.href = entry.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.title = entry.text || "";
      link.append(message);
      item.append(time, link);
    } else {
      item.append(time, message);
    }

    list.append(item);
  });
}

async function loadActivity() {
  try {
    const data = await getJSON("/api/activity");
    renderActivity(data.items);
  } catch {
    renderActivity([]);
  }
}

/* ----- discord username copy ----- */

async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const helper = document.createElement("textarea");
  helper.value = text;
  helper.setAttribute("readonly", "");
  helper.style.position = "fixed";
  helper.style.opacity = "0";
  document.body.append(helper);
  helper.select();
  const ok = document.execCommand("copy");
  helper.remove();
  if (!ok) throw new Error("copy failed");
}

function setupDiscord() {
  const card = $("#discord-card");
  if (!card) return;

  card.addEventListener("click", async () => {
    const username = card.dataset.username || "adamix";
    try {
      await copyText(username);
      card.textContent = "copied!";
    } catch {
      card.textContent = username;
    }
    card.classList.add("is-copied");
    window.setTimeout(() => {
      card.textContent = "Discord";
      card.classList.remove("is-copied");
    }, 1600);
  });
}

/* ----- game of life ----- */

function setupGameOfLife() {
  const canvas = $("#gol-canvas");
  if (!canvas) return;
  const context = canvas.getContext("2d");
  if (!context) return;

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const targetCell = 9;
  const frameDelay = 200;

  let cols = 0;
  let rows = 0;
  let cells = null;
  let raf = 0;
  let last = 0;
  let visible = true;

  function seed() {
    cells = new Uint8Array(cols * rows);
    for (let i = 0; i < cells.length; i += 1) {
      cells[i] = Math.random() < 0.26 ? 1 : 0;
    }
  }

  function resize() {
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (!width || !height) return;

    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    context.setTransform(dpr, 0, 0, dpr, 0, 0);

    const nextCols = Math.max(6, Math.floor(width / targetCell));
    const nextRows = Math.max(10, Math.floor(height / targetCell));
    if (nextCols === cols && nextRows === rows && cells) return;

    cols = nextCols;
    rows = nextRows;
    seed();
    draw();
  }

  function step() {
    const next = new Uint8Array(cols * rows);
    for (let y = 0; y < rows; y += 1) {
      const up = ((y - 1 + rows) % rows) * cols;
      const down = ((y + 1) % rows) * cols;
      const row = y * cols;
      for (let x = 0; x < cols; x += 1) {
        const left = (x - 1 + cols) % cols;
        const right = (x + 1) % cols;
        const neighbours =
          cells[up + left] +
          cells[up + x] +
          cells[up + right] +
          cells[row + left] +
          cells[row + right] +
          cells[down + left] +
          cells[down + x] +
          cells[down + right];
        const alive = cells[row + x];
        next[row + x] = alive ? (neighbours === 2 || neighbours === 3 ? 1 : 0) : neighbours === 3 ? 1 : 0;
      }
    }
    cells = next;
  }

  function draw() {
    if (!cells || !cols || !rows) return;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    const cellW = width / cols;
    const cellH = height / rows;

    context.clearRect(0, 0, width, height);
    context.fillStyle = "rgba(112, 214, 162, 0.8)";
    for (let y = 0; y < rows; y += 1) {
      for (let x = 0; x < cols; x += 1) {
        if (!cells[y * cols + x]) continue;
        context.fillRect(
          x * cellW + 1,
          y * cellH + 1,
          Math.max(1, cellW - 2),
          Math.max(1, cellH - 2),
        );
      }
    }
  }

  function loop(timestamp) {
    raf = window.requestAnimationFrame(loop);
    if (!visible) return;
    if (timestamp - last < frameDelay) return;
    last = timestamp;
    step();
    draw();
  }

  function play() {
    if (reduceMotion.matches || raf || !visible) return;
    raf = window.requestAnimationFrame(loop);
  }

  function pause() {
    if (!raf) return;
    window.cancelAnimationFrame(raf);
    raf = 0;
  }

  resize();
  if (reduceMotion.matches) {
    draw();
  } else {
    play();
  }

  const observer = new ResizeObserver(() => {
    resize();
    if (reduceMotion.matches) draw();
  });
  observer.observe(canvas);

  if ("IntersectionObserver" in window) {
    const intersection = new IntersectionObserver((entries) => {
      visible = entries.some((entry) => entry.isIntersecting);
      if (visible) {
        play();
      } else {
        pause();
      }
    });
    intersection.observe(canvas);
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      pause();
    } else {
      play();
    }
  });

  reduceMotion.addEventListener("change", () => {
    if (reduceMotion.matches) {
      pause();
      draw();
    } else {
      play();
    }
  });
}

/* ----- boot ----- */

loadNowPlaying();
loadTopTracks();
loadActivity();
setupDiscord();
setupGameOfLife();

window.setInterval(loadNowPlaying, NOW_PLAYING_INTERVAL);
window.setInterval(loadTopTracks, TOP_TRACKS_INTERVAL);
window.setInterval(loadActivity, ACTIVITY_INTERVAL);
