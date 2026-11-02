"""Reconhecimento de links e @ das plataformas."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

PLATFORMS = ("instagram", "tiktok", "youtube")
PLATFORM_EMOJI = {"instagram": "📸", "tiktok": "🎵", "youtube": "▶️"}
PLATFORM_NAME = {"instagram": "Instagram", "tiktok": "TikTok", "youtube": "YouTube"}

_IG_POST = re.compile(r"instagram\.com/(?:[\w.]+/)?(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)", re.I)
_TT_VIDEO = re.compile(r"tiktok\.com/@([\w.]+)/(?:video|photo)/(\d+)", re.I)
_TT_SHORT = re.compile(r"^(?:vm|vt)\.tiktok\.com$|tiktok\.com/t/", re.I)
_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_HANDLE = re.compile(r"^[A-Za-z0-9_.\-]{1,60}$")


@dataclass(frozen=True)
class PostLink:
    platform: str
    external_id: str      # shortcode (IG), video id (TikTok/YouTube)
    url: str              # URL canônica
    owner_hint: str | None = None  # @ do dono quando o link mostra (TikTok)


def is_short_tiktok(url: str) -> bool:
    parsed = urlparse(url if "://" in url else "https://" + url)
    return bool(_TT_SHORT.search(parsed.netloc) or _TT_SHORT.search(parsed.netloc + parsed.path))


def parse_post_url(raw: str) -> PostLink | None:
    url = raw.strip().strip("<>")
    if not url:
        return None
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.").removeprefix("m.")

    if host.endswith("instagram.com"):
        m = _IG_POST.search(host + parsed.path)
        if m:
            code = m.group(1)
            return PostLink("instagram", code, f"https://www.instagram.com/p/{code}/")

    if host.endswith("tiktok.com"):
        m = _TT_VIDEO.search(host + parsed.path)
        if m:
            user, vid = m.group(1).lower(), m.group(2)
            return PostLink("tiktok", vid, f"https://www.tiktok.com/@{user}/video/{vid}", owner_hint=user)

    vid = None
    if host == "youtu.be":
        vid = parsed.path.strip("/").split("/")[0]
    elif host.endswith("youtube.com"):
        if parsed.path == "/watch":
            vid = parse_qs(parsed.query).get("v", [None])[0]
        else:
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) >= 2 and parts[0] in ("shorts", "live", "embed"):
                vid = parts[1]
    if vid and _YT_ID.match(vid):
        return PostLink("youtube", vid, f"https://www.youtube.com/watch?v={vid}")

    return None


def normalize_handle(raw: str | None) -> str | None:
    """Aceita '@fulano', 'fulano' ou o link do perfil. Devolve 'fulano'."""
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    if "/" in text:
        parsed = urlparse(text if "://" in text else "https://" + text)
        parts = [p for p in parsed.path.split("/") if p]
        if not parts:
            return None
        text = parts[0]
        if text in ("c", "channel", "user") and len(parts) > 1:
            text = parts[1]
    text = text.lstrip("@").strip().lower()
    return text if _HANDLE.match(text) else None
