"""SpiritFX Academy course list served from our own server (Supabase), instead of spiritfx.org.

Tables: sfx_courses (one row per tutor playlist) and sfx_course_videos (cached lectures).
Lectures, tutor name and picture are pulled from YouTube with YOUTUBE_API_KEY and refreshed
in the background. Public endpoints (no login), shaped like the app's existing course models:
  GET /api/public/courses?lvl=Beginner|Advanced|Robot|It|Crypto
  GET /api/public/courses/{cid}/videos
"""
import os
import json
import asyncio
import traceback
from datetime import datetime, timezone, timedelta

import requests
from aiohttp import web

YT_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
RESYNC_HOURS = 12
_deps = {}


def _sb(path, method="GET", body=None, prefer=None):
    h = dict(_deps["sb_headers"]())
    if prefer:
        h["Prefer"] = prefer
    url = f"{_deps['supabase_url']}/rest/v1/{path}"
    r = requests.request(method, url, headers=h, data=json.dumps(body) if body is not None else None, timeout=20)
    try:
        return r.status_code, (r.json() if r.text else None)
    except Exception:
        return r.status_code, None


def _j(data, status=200):
    return web.json_response(data, status=status, headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=60"})


async def courses_handler(request):
    lvl = request.query.get("lvl", "").strip()
    if lvl not in ("Beginner", "Advanced", "Robot", "It", "Crypto"):
        return _j([])
    st, rows = await asyncio.to_thread(
        _sb, f"sfx_courses?lvl=eq.{lvl}&active=eq.true&video_count=gt.0&select=*&order=sort.asc,created_at.asc")
    if st != 200 or not isinstance(rows, list):
        return _j([])
    out = []
    for r in rows:
        out.append({
            "courseId": r["course_id"], "title": r["title"], "creatorId": r["course_id"],
            "tutor": r["tutor"], "videoCount": str(r["video_count"]), "lvl": r["lvl"],
            "playlistId": r["playlist_id"], "language": r["language"], "descr": r["descr"],
            "img": r["img"], "telegram": "", "youtube": "", "x": "", "instagram": "",
            "datetime": r.get("created_at") or "", "region": r.get("region") or "",
        })
    return _j(out)


async def videos_handler(request):
    cid = request.match_info["cid"]
    if not cid.replace("_", "").replace("-", "").isalnum():
        return _j([])
    st, rows = await asyncio.to_thread(
        _sb, f"sfx_course_videos?course_id=eq.{cid}&select=sn,title,video_id&order=sn.asc")
    if st != 200 or not isinstance(rows, list):
        return _j([])
    return _j([{"sn": str(r["sn"]), "courseId": cid, "title": r["title"], "videoId": r["video_id"], "datetime": ""} for r in rows])


# ------------------------------------------------------------------ YouTube sync

def _yt(endpoint, **params):
    params["key"] = YT_KEY
    r = requests.get(f"https://www.googleapis.com/youtube/v3/{endpoint}", params=params, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"YouTube {endpoint} {r.status_code}: {r.text[:200]}")
    return r.json()


def sync_course(row):
    cid, pid = row["course_id"], row["playlist_id"]
    info = _yt("playlists", part="snippet", id=pid)
    items = info.get("items") or []
    if not items:
        raise RuntimeError("playlist not found or private")
    sn = items[0]["snippet"]
    channel_id, channel_title = sn.get("channelId"), sn.get("channelTitle", "")

    vids, token = [], None
    while True:
        p = {"part": "snippet", "playlistId": pid, "maxResults": 50}
        if token:
            p["pageToken"] = token
        d = _yt("playlistItems", **p)
        for it in d.get("items", []):
            s = it["snippet"]
            t = (s.get("title") or "").strip()
            vid = (s.get("resourceId") or {}).get("videoId")
            if not vid or t in ("Private video", "Deleted video"):
                continue
            vids.append((t, vid))
        token = d.get("nextPageToken")
        if not token or len(vids) >= 300:
            break

    avatar = ""
    if channel_id:
        try:
            ch = _yt("channels", part="snippet", id=channel_id)
            th = ((ch.get("items") or [{}])[0].get("snippet") or {}).get("thumbnails") or {}
            avatar = (th.get("medium") or th.get("default") or {}).get("url", "")
        except Exception:
            pass

    _sb(f"sfx_course_videos?course_id=eq.{cid}", "DELETE")
    for i in range(0, len(vids), 100):
        chunk = [{"course_id": cid, "sn": i + j + 1, "title": t[:200], "video_id": v} for j, (t, v) in enumerate(vids[i:i + 100])]
        _sb("sfx_course_videos", "POST", chunk)
    patch = {"video_count": len(vids), "synced_at": datetime.now(timezone.utc).isoformat()}
    if not row.get("tutor"):
        patch["tutor"] = channel_title
    if not row.get("img") and avatar:
        patch["img"] = avatar
    _sb(f"sfx_courses?course_id=eq.{cid}", "PATCH", patch)
    return len(vids)


def sync_all(force=False):
    if not YT_KEY:
        print("[COURSES] YOUTUBE_API_KEY not set, skipping sync")
        return
    st, rows = _sb("sfx_courses?active=eq.true&select=*")
    if st != 200 or not isinstance(rows, list):
        return
    cutoff = datetime.now(timezone.utc) - timedelta(hours=RESYNC_HOURS)
    for r in rows:
        try:
            sa = r.get("synced_at")
            if not force and sa and datetime.fromisoformat(sa.replace("Z", "+00:00")) > cutoff:
                continue
            n = sync_course(r)
            print(f"[COURSES] synced {r['course_id']}: {n} lectures")
        except Exception as e:
            print(f"[COURSES] sync failed for {r['course_id']}: {type(e).__name__}: {e}")


async def _loop(app):
    await asyncio.sleep(25)
    while True:
        try:
            await asyncio.to_thread(sync_all)
        except Exception:
            traceback.print_exc()
        await asyncio.sleep(3 * 3600)


async def _on_start(app):
    app["courses_task"] = asyncio.create_task(_loop(app))


async def _on_cleanup(app):
    t = app.get("courses_task")
    if t:
        t.cancel()


def register(app, sb_headers, supabase_url):
    _deps.update(sb_headers=sb_headers, supabase_url=supabase_url)
    app.router.add_get("/api/public/courses", courses_handler)
    app.router.add_get("/api/public/courses/{cid}/videos", videos_handler)
    app.on_startup.append(_on_start)
    app.on_cleanup.append(_on_cleanup)
