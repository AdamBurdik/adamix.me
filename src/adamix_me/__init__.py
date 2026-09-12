from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse

STATIC_DIR = Path(__file__).parent / "static"

LISTENBRAINZ_USER = "adamix"
LISTENBRAINZ_URL = (
    f"https://api.listenbrainz.org/1/user/{LISTENBRAINZ_USER}/playing-now"
)
DEEZER_SEARCH_URL = "https://api.deezer.com/search"

app = FastAPI(title="adamix.me")

_client = httpx.AsyncClient(timeout=10.0)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/now-playing")
async def now_playing():
    try:
        resp = await _client.get(LISTENBRAINZ_URL)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return {"playing_now": False}

    listens = (data.get("payload") or {}).get("listens") or []
    if not data.get("payload", {}).get("playing_now") or not listens:
        return {"playing_now": False}

    meta = listens[0].get("track_metadata") or {}
    title = meta.get("track_name") or "Unknown track"
    artist = meta.get("artist_name") or "Unknown artist"
    album = (
        meta.get("release_name")
        or meta.get("album_name")
        or "Unknown album"
    )
    duration_ms = (meta.get("additional_info") or {}).get("duration_ms")

    cover_url, avatar_url, track_url = await _search_deezer(
        title, artist, album, duration_ms
    )

    return {
        "playing_now": True,
        "title": title,
        "artist": artist,
        "album": album,
        "duration_ms": duration_ms,
        "cover_url": cover_url,
        "avatar_url": avatar_url,
        "track_url": track_url,
    }


async def _search_deezer(
    title: str, artist: str, album: str, duration_ms: int | None
) -> tuple[str | None, str | None, str | None]:
    t = title.lower()
    a = artist.lower()
    al = album.lower()
    dm = duration_ms

    def score(candidate) -> int:
        ct = (candidate.get("title") or "").lower()
        ca = (candidate.get("artist") or {}).get("name", "").lower()
        cal = ((candidate.get("album") or {}).get("title") or "").lower()
        cd = candidate.get("duration")

        s = 0
        if ct == t:
            s += 4
        elif t in ct:
            s += 2
        if ca == a:
            s += 4
        elif a in ca:
            s += 2
        if al != "unknown album":
            if cal == al:
                s += 6
            elif al in cal or cal in al:
                s += 2
        if dm and cd:
            delta = abs(cd * 1000 - dm)
            if delta <= 2000:
                s += 4
            elif delta <= 5000:
                s += 1
        return s

    async def search(query: str) -> list:
        try:
            resp = await _client.get(DEEZER_SEARCH_URL, params={"q": query, "limit": 10})
            resp.raise_for_status()
            return resp.json().get("data") or []
        except Exception:
            return []

    queries = [f"{title} {artist}"]
    if al != "unknown album":
        queries.insert(0, f"{title} {artist} {album}")

    best = None
    for q in queries:
        results = await search(q)
        best = max(results, key=score, default=None)
        if best is not None and score(best) >= 4:
            break

    if best is None or score(best) < 4:
        return None, None, None

    return (
        (best.get("album") or {}).get("cover_medium"),
        (best.get("artist") or {}).get("picture_medium"),
        best.get("link"),
    )


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main() -> None:
    import uvicorn

    uvicorn.run("adamix_me:app", host="0.0.0.0", port=8000, reload=True)
