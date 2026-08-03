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

    cover_url, avatar_url = await _search_deezer(title, artist)

    return {
        "playing_now": True,
        "title": title,
        "artist": artist,
        "album": album,
        "duration_ms": duration_ms,
        "cover_url": cover_url,
        "avatar_url": avatar_url,
    }


async def _search_deezer(title: str, artist: str) -> tuple[str | None, str | None]:
    query = f'track:"{title}" artist:"{artist}"'
    try:
        resp = await _client.get(DEEZER_SEARCH_URL, params={"q": query, "limit": 5})
        resp.raise_for_status()
        results = resp.json().get("data") or []
    except Exception:
        return None, None

    t = title.lower()
    a = artist.lower()

    def score(candidate) -> int:
        ct = (candidate.get("title") or "").lower()
        ca = (candidate.get("artist") or {}).get("name", "").lower()
        if ct == t and ca == a:
            return 3
        if ct == t or ca == a:
            return 2
        return 1

    best = max(results, key=score, default=None)
    if best is None:
        return None, None

    return (
        (best.get("album") or {}).get("cover_medium"),
        (best.get("artist") or {}).get("picture_medium"),
    )


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main() -> None:
    import uvicorn

    uvicorn.run("adamix_me:app", host="0.0.0.0", port=8000, reload=True)
