from __future__ import annotations

import asyncio
import base64
import hmac
import html
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.responses import FileResponse

STATIC_DIR = Path(__file__).parent / "static"
DATA_DIR = Path(__file__).parent / "data"
LOG_FILE = DATA_DIR / "log.json"

LISTENBRAINZ_USER = "adamix"
LISTENBRAINZ_URL = (
    f"https://api.listenbrainz.org/1/user/{LISTENBRAINZ_USER}/playing-now"
)
LISTENBRAINZ_TOP_URL = (
    f"https://api.listenbrainz.org/1/stats/user/{LISTENBRAINZ_USER}/recordings"
)
DEEZER_SEARCH_URL = "https://api.deezer.com/search"
ITUNES_SEARCH_URL = "https://itunes.apple.com/search"

GITHUB_USER = "AdamBurdik"
GITHUB_EVENTS_URL = f"https://api.github.com/users/{GITHUB_USER}/events/public"
GITHUB_FEED_URL = f"https://github.com/{GITHUB_USER}.atom"
GITHUB_REPO = os.getenv("ADAMIX_GITHUB_REPO", "AdamBurdik/adamix.me")
GITHUB_BRANCH = os.getenv("ADAMIX_GITHUB_BRANCH", "main")
GITHUB_LOG_PATH = os.getenv(
    "ADAMIX_LOG_PATH", "src/adamix_me/data/log.json"
)

TOP_TRACKS_TTL = 300
ACTIVITY_TTL = 600
ACTIVITY_EMPTY_TTL = 30
ACTIVITY_LIMIT = 6
MANUAL_LOG_LIMIT = 100

USER_AGENT = "adamix.me"

app = FastAPI(title="adamix.me")

_client = httpx.AsyncClient(timeout=10.0)
_cache: dict[str, tuple[float, Any]] = {}


def _cache_get(key: str) -> Any | None:
    hit = _cache.get(key)
    if hit is None:
        return None
    if hit[0] <= time.monotonic():
        _cache.pop(key, None)
        return None
    return hit[1]


def _cache_set(key: str, value: Any, ttl: int) -> None:
    _cache[key] = (time.monotonic() + ttl, value)


def _github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _parse_time(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/now-playing")
async def now_playing():
    try:
        resp = await _client.get(
            LISTENBRAINZ_URL, headers={"User-Agent": USER_AGENT}
        )
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
    if not cover_url:
        cover_url = await _search_itunes(title, artist, album)

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
            resp = await _client.get(
                DEEZER_SEARCH_URL,
                params={"q": query, "limit": 10},
                headers={"User-Agent": USER_AGENT},
            )
            resp.raise_for_status()
            return resp.json().get("data") or []
        except Exception:
            return []

    queries = [f"{title} {artist}"]
    if al != "unknown album":
        queries.append(f"{title} {artist} {album}")

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


async def _search_itunes(title: str, artist: str, album: str) -> str | None:
    try:
        resp = await _client.get(
            ITUNES_SEARCH_URL,
            params={
                "term": f"{title} {artist}",
                "entity": "song",
                "limit": 10,
            },
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        results = resp.json().get("results") or []
    except Exception:
        return None

    t = title.lower()
    a = artist.lower()
    al = album.lower()

    def tokens(value: str) -> set[str]:
        return {word for word in re.findall(r"[a-z0-9]+", value) if len(word) > 2}

    def score(candidate: dict[str, Any]) -> int:
        ct = (candidate.get("trackName") or "").lower()
        ca = (candidate.get("artistName") or "").lower()
        cal = (candidate.get("collectionName") or "").lower()

        s = 0
        if ct == t:
            s += 4
        elif t in ct or ct in t:
            s += 2
        if ca == a:
            s += 4
        elif a in ca or ca in a:
            s += 2
        if al != "unknown album":
            if cal == al:
                s += 6
            elif al in cal or cal in al:
                s += 2
            else:
                s += min(len(tokens(al) & tokens(cal)), 5)
        return s

    best = max(results, key=score, default=None)
    if best is None or score(best) < 4:
        return None

    artwork = best.get("artworkUrl100")
    if not isinstance(artwork, str) or not artwork:
        return None
    return artwork.replace("100x100bb", "600x600bb")


@app.get("/api/top-tracks")
async def top_tracks():
    cached = _cache_get("top-tracks")
    if cached is not None:
        return {"tracks": cached}

    tracks = await _fetch_top_tracks()
    if tracks:
        _cache_set("top-tracks", tracks, TOP_TRACKS_TTL)
    return {"tracks": tracks}


async def _fetch_top_tracks() -> list[dict[str, Any]]:
    try:
        resp = await _client.get(
            LISTENBRAINZ_TOP_URL,
            params={"range": "week", "count": 3},
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        payload = resp.json().get("payload") or {}
    except Exception:
        return []

    tracks: list[dict[str, Any]] = []
    for item in (payload.get("recordings") or [])[:3]:
        title = item.get("track_name")
        if not title:
            continue
        tracks.append(
            {
                "title": title,
                "artist": item.get("artist_name") or "",
                "album": item.get("release_name") or "",
                "count": item.get("listen_count") or 0,
            }
        )
    return tracks


@app.get("/api/activity")
async def activity():
    cached = _cache_get("activity")
    if cached is not None:
        return {"items": cached}

    items: list[dict[str, Any]] = []

    for entry in await _read_manual_entries():
        if not isinstance(entry, dict):
            continue
        text = entry.get("text")
        at = entry.get("at")
        if not isinstance(text, str) or not text.strip() or not isinstance(at, str):
            continue
        item = {"at": at, "text": text.strip(), "source": "manual"}
        if isinstance(entry.get("url"), str):
            item["url"] = entry["url"]
        items.append(item)

    github_ok = False
    try:
        resp = await _client.get(
            GITHUB_EVENTS_URL,
            params={"per_page": 30},
            headers=_github_headers(),
        )
        if resp.status_code == 200:
            for event in resp.json():
                item = _format_event(event)
                if item is not None:
                    items.append(item)
            github_ok = True
    except Exception:
        pass

    if not github_ok:
        items.extend(_feed_events(await _fetch_github_feed()))

    items.sort(key=lambda item: _parse_time(item.get("at")), reverse=True)
    items = items[:ACTIVITY_LIMIT]
    await _enrich_pushes(items)
    for item in items:
        item.pop("_commit", None)
        item.pop("_repo", None)
    _cache_set(
        "activity",
        items,
        ACTIVITY_TTL if items else ACTIVITY_EMPTY_TTL,
    )
    return {"items": items}


async def _fetch_github_feed() -> str:
    try:
        resp = await _client.get(
            GITHUB_FEED_URL, headers={"User-Agent": USER_AGENT}
        )
        resp.raise_for_status()
        return resp.text
    except Exception:
        return ""


def _feed_events(payload: str) -> list[dict[str, Any]]:
    if not payload:
        return []

    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items: list[dict[str, Any]] = []
    for entry in root.findall("atom:entry", ns):
        title = entry.findtext("atom:title", default="", namespaces=ns).strip()
        published = entry.findtext(
            "atom:published", default="", namespaces=ns
        ).strip()
        link = entry.find("atom:link", ns)
        url = link.get("href") if link is not None else None
        if not title or not published:
            continue

        try:
            at = datetime.strptime(
                published, "%Y-%m-%d %H:%M:%S %Z"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        text = title
        if " pushed " in title:
            repo = title.split(" pushed ", 1)[1].strip()
            content = entry.findtext(
                "atom:content", default="", namespaces=ns
            )
            match = re.search(
                r"<blockquote>\s*(.*?)\s*</blockquote>", content, re.S
            )
            message = ""
            if match:
                message = re.sub(r"<[^>]+>", " ", match.group(1))
                message = " ".join(html.unescape(message).split())
            text = f"{message} · {repo}" if message else f"pushed {repo}"

        item = {
            "at": at.isoformat(),
            "text": text,
            "source": "github",
        }
        if isinstance(url, str):
            item["url"] = url
        items.append(item)
    return items


async def _enrich_pushes(items: list[dict[str, Any]]) -> None:
    targets = [item for item in items if item.get("_commit")][:4]
    if not targets:
        return

    async def message_for(item: dict[str, Any]) -> str | None:
        repo_full, sha = item["_commit"]
        try:
            resp = await _client.get(
                f"https://api.github.com/repos/{repo_full}/commits/{sha}",
                headers=_github_headers(),
            )
            if resp.status_code != 200:
                return None
            message = ((resp.json().get("commit") or {}).get("message") or "")
            lines = message.splitlines()
            return lines[0].strip() if lines else None
        except Exception:
            return None

    messages = await asyncio.gather(
        *(message_for(item) for item in targets)
    )
    for item, message in zip(targets, messages):
        if message:
            item["text"] = f"{message} · {item.get('_repo') or ''}".rstrip(
                " ·"
            )


def _format_event(event: dict[str, Any]) -> dict[str, Any] | None:
    kind = event.get("type")
    payload = event.get("payload") or {}
    repo_full = (event.get("repo") or {}).get("name") or ""
    repo = repo_full.rsplit("/", 1)[-1]
    at = event.get("created_at")
    url = f"https://github.com/{repo_full}" if repo_full else None
    text: str | None = None
    commit_ref: tuple[str, str] | None = None

    if kind == "PushEvent":
        commits = payload.get("commits") or []
        head = payload.get("head")
        if commits:
            commit = commits[-1]
            message = (commit.get("message") or "").splitlines()
            if message and message[0].strip():
                text = f"{message[0].strip()} · {repo}"
            sha = commit.get("sha")
            if sha and repo_full:
                url = f"https://github.com/{repo_full}/commit/{sha}"
        else:
            text = f"pushed to {repo}"
            if head and repo_full:
                url = f"https://github.com/{repo_full}/commit/{head}"
                commit_ref = (repo_full, head)
    elif kind == "ReleaseEvent":
        release = payload.get("release") or {}
        tag = release.get("tag_name") or "release"
        text = f"released {tag} · {repo}"
        url = release.get("html_url") or url
    elif kind == "PullRequestEvent":
        pull = payload.get("pull_request") or {}
        number = pull.get("number") or payload.get("number")
        action = payload.get("action")
        if pull.get("merged"):
            text = f"merged PR #{number} · {repo}"
        elif action in ("opened", "closed", "reopened"):
            text = f"{action} PR #{number} · {repo}"
        url = pull.get("html_url") or url
    elif kind == "CreateEvent":
        ref_type = payload.get("ref_type")
        ref = payload.get("ref")
        if ref_type == "repository":
            text = f"created repository {repo}"
        elif ref_type and ref:
            text = f"created {ref_type} {ref} · {repo}"
    elif kind == "IssuesEvent":
        issue = payload.get("issue") or {}
        action = payload.get("action")
        number = issue.get("number")
        if action in ("opened", "closed", "reopened"):
            text = f"{action} issue #{number} · {repo}"
        url = issue.get("html_url") or url

    if not text or not isinstance(at, str):
        return None

    item = {"at": at, "text": text, "url": url, "source": "github"}
    if commit_ref is not None:
        item["_commit"] = commit_ref
        item["_repo"] = repo
    return item


class LogEntry(BaseModel):
    text: str = Field(min_length=1, max_length=280)
    at: datetime | None = None


@app.post("/api/activity", status_code=201)
async def add_activity(
    entry: LogEntry, authorization: str | None = Header(default=None)
):
    token = os.getenv("ADAMIX_LOG_TOKEN")
    if not token:
        raise HTTPException(
            status_code=503, detail="manual logging is not configured"
        )
    expected = f"Bearer {token}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="invalid token")

    text = entry.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="text must not be blank")

    when = entry.at or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    item = {
        "at": when.isoformat(),
        "text": text,
        "source": "manual",
    }

    entries = await _read_manual_entries()
    entries.append(item)
    entries = [
        value
        for value in entries
        if isinstance(value, dict)
    ][-MANUAL_LOG_LIMIT:]

    await _write_manual_entries(entries)
    _cache.pop("activity", None)
    return {"item": item}


def _read_local_entries() -> list[dict[str, Any]]:
    try:
        data = json.loads(LOG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


async def _read_manual_entries() -> list[dict[str, Any]]:
    token = os.getenv("GITHUB_TOKEN")
    if token:
        try:
            resp = await _client.get(
                f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_LOG_PATH}",
                params={"ref": GITHUB_BRANCH},
                headers=_github_headers(),
            )
            if resp.status_code == 200:
                content = resp.json().get("content") or ""
                data = json.loads(base64.b64decode(content).decode("utf-8"))
                if isinstance(data, list):
                    return data
        except Exception:
            pass
    return _read_local_entries()


async def _write_manual_entries(entries: list[dict[str, Any]]) -> None:
    payload = json.dumps(entries, indent=2, ensure_ascii=False) + "\n"
    saved = False

    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        LOG_FILE.write_text(payload, encoding="utf-8")
        saved = True
    except OSError:
        pass

    token = os.getenv("GITHUB_TOKEN")
    if token:
        api_url = (
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_LOG_PATH}"
        )
        try:
            current = await _client.get(
                api_url,
                params={"ref": GITHUB_BRANCH},
                headers=_github_headers(),
            )
            sha = current.json().get("sha") if current.status_code == 200 else None
            body: dict[str, Any] = {
                "message": "log: add entry",
                "content": base64.b64encode(payload.encode("utf-8")).decode(),
                "branch": GITHUB_BRANCH,
            }
            if sha:
                body["sha"] = sha
            resp = await _client.put(api_url, headers=_github_headers(), json=body)
            if resp.status_code in (200, 201):
                saved = True
        except Exception:
            pass

    if not saved:
        raise HTTPException(
            status_code=500, detail="could not persist log entry"
        )


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main() -> None:
    import uvicorn

    uvicorn.run("adamix_me:app", host="0.0.0.0", port=8000, reload=True)
