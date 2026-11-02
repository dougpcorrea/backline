"""Coleta de bios e comentários.

- YouTube: API oficial (YouTube Data API v3).
- Instagram e TikTok: actors da Apify, chamados pela API REST.

Cada actor da Apify devolve campos com nomes um pouco diferentes, então a
leitura dos resultados é defensiva (tenta vários nomes de campo).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp

from .config import Config

log = logging.getLogger("backline.collectors")

YT_API = "https://www.googleapis.com/youtube/v3"
APIFY_API = "https://api.apify.com/v2"


class CollectorError(Exception):
    """Falha ao consultar uma plataforma (sem chave, fora do ar, bloqueio...)."""


@dataclass
class Comment:
    comment_id: str
    author_handle: str | None
    author_external_id: str | None
    created_at: int | None


@dataclass
class Profile:
    handle: str
    bio: str
    external_id: str | None = None


def _first(d: dict, *keys: str) -> Any:
    for key in keys:
        cur: Any = d
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                cur = None
                break
        if cur not in (None, ""):
            return cur
    return None


def _to_ts(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value / 1000) if value > 10_000_000_000 else int(value)
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


class Collectors:
    def __init__(self, cfg: Config, session: aiohttp.ClientSession):
        self.cfg = cfg
        self.session = session

    # ================= YouTube =================
    async def _yt(self, path: str, **params) -> dict:
        if not self.cfg.youtube_api_key:
            raise CollectorError("YOUTUBE_API_KEY não configurada")
        params["key"] = self.cfg.youtube_api_key
        async with self.session.get(f"{YT_API}/{path}", params=params, timeout=aiohttp.ClientTimeout(total=30)) as r:
            data = await r.json(content_type=None)
            if r.status == 403 and "commentsDisabled" in str(data):
                return {"items": []}
            if r.status >= 400:
                raise CollectorError(f"YouTube {r.status}: {str(data)[:200]}")
            return data

    async def youtube_profile(self, handle: str) -> Profile | None:
        data = await self._yt("channels", part="snippet", forHandle="@" + handle)
        items = data.get("items") or []
        if not items:
            return None
        ch = items[0]
        return Profile(handle, ch["snippet"].get("description", ""), ch["id"])

    async def youtube_video_channel(self, video_id: str) -> str | None:
        data = await self._yt("videos", part="snippet", id=video_id)
        items = data.get("items") or []
        return items[0]["snippet"]["channelId"] if items else None

    async def youtube_comments(self, video_id: str, limit: int = 2000) -> list[Comment]:
        out: list[Comment] = []
        token = None
        while len(out) < limit:
            params = dict(part="snippet", videoId=video_id, maxResults=100, textFormat="plainText")
            if token:
                params["pageToken"] = token
            data = await self._yt("commentThreads", **params)
            for item in data.get("items", []):
                s = item["snippet"]["topLevelComment"]["snippet"]
                display = (s.get("authorDisplayName") or "").lstrip("@").lower() or None
                out.append(Comment(
                    comment_id=item["id"],
                    author_handle=display,
                    author_external_id=_first(s, "authorChannelId.value"),
                    created_at=_to_ts(s.get("publishedAt")),
                ))
            token = data.get("nextPageToken")
            if not token:
                break
        return out

    # ================= Apify =================
    async def _apify(self, actor: str, payload: dict) -> list[dict]:
        if not self.cfg.apify_token:
            raise CollectorError("APIFY_TOKEN não configurado")
        url = f"{APIFY_API}/acts/{actor}/run-sync-get-dataset-items"
        params = {"token": self.cfg.apify_token, "timeout": "240"}
        try:
            async with self.session.post(url, params=params, json=payload,
                                         timeout=aiohttp.ClientTimeout(total=300)) as r:
                data = await r.json(content_type=None)
                if r.status >= 400:
                    raise CollectorError(f"Apify {actor} {r.status}: {str(data)[:200]}")
        except aiohttp.ClientError as e:
            raise CollectorError(f"Apify {actor}: {e}") from e
        if not isinstance(data, list):
            raise CollectorError(f"Apify {actor}: resposta inesperada")
        # Alguns actors devolvem um item {"error": ...} quando não acham nada
        return [d for d in data if isinstance(d, dict) and not d.get("error")]

    async def instagram_profile(self, handle: str) -> Profile | None:
        items = await self._apify(self.cfg.apify_ig_profile, {"usernames": [handle]})
        for it in items:
            if (it.get("username") or "").lower() == handle:
                return Profile(handle, it.get("biography") or "", str(it.get("id") or "") or None)
        return None

    async def tiktok_profile(self, handle: str) -> Profile | None:
        items = await self._apify(self.cfg.apify_tt_profile, {"profiles": [handle], "resultsPerPage": 1})
        for it in items:
            name = (_first(it, "authorMeta.name", "uniqueId", "username") or "").lower()
            if name == handle:
                bio = _first(it, "authorMeta.signature", "signature", "bio") or ""
                return Profile(handle, bio, str(_first(it, "authorMeta.id", "id") or "") or None)
        return None

    async def instagram_comments_batch(self, urls: list[str], limit: int = 300) -> dict[str, list[Comment]] | None:
        """Lê comentários de vários posts numa execução só do Apify. None = não deu pra separar por post."""
        items = await self._apify(self.cfg.apify_ig_comments, {"directUrls": urls, "resultsLimit": limit})
        codes = {u: (re.search(r"/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)", u) or [None, u])[1] for u in urls}
        out: dict[str, list[Comment]] = {u: [] for u in urls}
        for it in items:
            src = " ".join(str(it.get(k) or "") for k in ("postUrl", "inputUrl", "url", "postShortCode", "shortCode"))
            target = next((u for u, c in codes.items() if c and c in src), None)
            if target is None:
                return None
            c = self._ig_comment(it)
            if c:
                out[target].append(c)
        return out

    def _ig_comment(self, it: dict) -> "Comment | None":
        cid = _first(it, "id", "commentId", "pk")
        if not cid:
            return None
        return Comment(
            comment_id=str(cid),
            author_handle=(_first(it, "ownerUsername", "owner.username", "username") or "").lower() or None,
            author_external_id=str(_first(it, "owner.id", "ownerId") or "") or None,
            created_at=_to_ts(_first(it, "timestamp", "createdAt")),
        )

    async def instagram_comments(self, url: str, limit: int = 1000) -> list[Comment]:
        items = await self._apify(self.cfg.apify_ig_comments, {"directUrls": [url], "resultsLimit": limit})
        out = []
        for it in items:
            cid = _first(it, "id", "commentId", "pk")
            if not cid:
                continue
            out.append(Comment(
                comment_id=str(cid),
                author_handle=(_first(it, "ownerUsername", "owner.username", "username") or "").lower() or None,
                author_external_id=str(_first(it, "owner.id", "ownerId") or "") or None,
                created_at=_to_ts(_first(it, "timestamp", "createdAt")),
            ))
        return out

    async def tiktok_comments(self, url: str, limit: int = 1000) -> list[Comment]:
        items = await self._apify(self.cfg.apify_tt_comments,
                                  {"postURLs": [url], "commentsPerPost": limit, "maxRepliesPerComment": 0})
        out = []
        for it in items:
            cid = _first(it, "cid", "id", "commentId")
            if not cid:
                continue
            out.append(Comment(
                comment_id=str(cid),
                author_handle=(_first(it, "uniqueId", "user.uniqueId", "username") or "").lower() or None,
                author_external_id=str(_first(it, "uid", "user.uid", "userId") or "") or None,
                created_at=_to_ts(_first(it, "createTime", "createTimeISO")),
            ))
        return out

    # ================= Mutual Follow =================
    async def youtube_is_subscribed(self, subscriber_channel: str, target_channel: str) -> bool | None:
        """True/False se dá pra saber; None se as inscrições do canal são privadas."""
        if not self.cfg.youtube_api_key:
            raise CollectorError("YOUTUBE_API_KEY não configurada")
        params = {"part": "id", "channelId": subscriber_channel, "forChannelId": target_channel,
                  "maxResults": 1, "key": self.cfg.youtube_api_key}
        async with self.session.get(f"{YT_API}/subscriptions", params=params,
                                    timeout=aiohttp.ClientTimeout(total=30)) as r:
            data = await r.json(content_type=None)
            if r.status == 403 and "subscriptionForbidden" in str(data):
                return None
            if r.status >= 400:
                raise CollectorError(f"YouTube {r.status}: {str(data)[:200]}")
        return bool(data.get("items"))

    async def following(self, platform: str, handle: str, limit: int) -> set[str]:
        """Quem o perfil segue (mais recentes primeiro), em minúsculas e sem @."""
        if platform == "instagram":
            items = await self._apify(self.cfg.apify_ig_following,
                                      {"username": handle, "scrape_type": "following", "max_items": limit})
        elif platform == "tiktok":
            items = await self._apify(self.cfg.apify_tt_following,
                                      {"profiles": [handle], "mode": "following", "maxItemsPerProfile": limit})
        else:
            raise ValueError(platform)
        out = set()
        for it in items:
            name = _first(it, "username", "uniqueId", "user.username", "user.uniqueId", "reel.owner.username")
            if name and str(name).lower().lstrip("@") != handle:
                out.add(str(name).lower().lstrip("@"))
        return out

    # ================= fachada =================
    async def profile(self, platform: str, handle: str) -> Profile | None:
        if platform == "youtube":
            return await self.youtube_profile(handle)
        if platform == "instagram":
            return await self.instagram_profile(handle)
        if platform == "tiktok":
            return await self.tiktok_profile(handle)
        raise ValueError(platform)

    async def comments(self, platform: str, external_id: str, url: str) -> list[Comment]:
        if platform == "youtube":
            return await self.youtube_comments(external_id)
        if platform == "instagram":
            return await self.instagram_comments(url)
        if platform == "tiktok":
            return await self.tiktok_comments(url)
        raise ValueError(platform)

    async def post_details(self, platform: str, url: str, external_id: str) -> dict:
        """Confere se a publicação existe e traz título, capa e dono.

        found: True (existe), False (não existe/privada) ou None (não deu para conferir agora).
        YouTube/TikTok usam oEmbed (grátis); Instagram usa Apify (frações de centavo por post).
        """
        if platform == "instagram":
            return await self.instagram_post_details(url)
        endpoints = {"youtube": "https://www.youtube.com/oembed", "tiktok": "https://www.tiktok.com/oembed"}
        out = {"found": None, "title": None, "thumb": None, "owner": None}
        if platform not in endpoints:
            return out
        try:
            async with self.session.get(endpoints[platform], params={"url": url, "format": "json"},
                                        timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    out.update(found=True, title=data.get("title") or None, thumb=data.get("thumbnail_url"))
                    author = data.get("author_unique_id") or data.get("author_url") or ""
                    out["owner"] = author.rstrip("/").rsplit("@", 1)[-1].lower() or None
                elif r.status in (400, 401, 403, 404):
                    out["found"] = False
        except (aiohttp.ClientError, ValueError):
            pass
        if platform == "youtube" and out["found"] is not False:
            out["thumb"] = f"https://i.ytimg.com/vi/{external_id}/hqdefault.jpg"  # não expira
        return out

    async def post_meta(self, platform: str, url: str, external_id: str) -> tuple[str | None, str | None]:
        d = await self.post_details(platform, url, external_id)
        return d["title"], d["thumb"]

    async def instagram_post_details(self, url: str) -> dict:
        """Capa, 'título' (1ª linha da legenda) e dono de um post do Instagram via Apify. A URL da capa
        expira em poucos dias, por isso o Discover copia a imagem para o armazenamento próprio."""
        out = {"found": None, "title": None, "thumb": None, "owner": None}
        try:
            items = await self._apify(self.cfg.apify_ig_post, {
                "directUrls": [url], "resultsType": "posts", "resultsLimit": 1, "addParentData": False})
        except CollectorError:
            return out
        items = [i for i in items if i.get("displayUrl") or i.get("shortCode") or i.get("ownerUsername")]
        if not items:
            out["found"] = False
            return out
        it = items[0]
        out["found"] = True
        out["owner"] = (it.get("ownerUsername") or "").lower() or None
        for line in (it.get("caption") or "").splitlines():
            line = re.sub(r"[#@]\S+", "", line).strip(" -–—·|")
            if len(line) >= 2:
                out["title"] = line[:70].rstrip() + ("…" if len(line) > 70 else "")
                break
        if not out["title"] and out["owner"]:
            out["title"] = f"@{out['owner']} on Instagram"
        out["thumb"] = it.get("displayUrl") or (it.get("images") or [None])[0]
        return out

    async def resolve_redirect(self, url: str) -> str:
        """Expande links curtos (ex.: vm.tiktok.com)."""
        try:
            async with self.session.get(url, allow_redirects=True,
                                        timeout=aiohttp.ClientTimeout(total=15)) as r:
                return str(r.url)
        except aiohttp.ClientError:
            return url
