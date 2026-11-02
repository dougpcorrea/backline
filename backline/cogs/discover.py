"""#discover: grade de trabalhos da comunidade.

No canal fica uma prévia da grade (galeria de quadros com capa + título/artista/nível) e o botão
"Open Discover", que abre a Activity (mini-app dentro do Discord, pasta docs/) onde cada quadro é clicável.
O bot publica os dados da Activity (discover.json + capas) num bucket público do Supabase Storage.
"""
from __future__ import annotations

import asyncio
import io
import random
from collections import OrderedDict

import aiohttp
import discord
from discord.ext import commands, tasks

from ..db import now
from ..discover import DAY, NEW_ARTIST_DAYS, Candidate, Viewer, pick
from ..grid import Tile, render_tile
from ..links import PLATFORM_EMOJI, PLATFORM_NAME

BATCH = 6
POOL_DAYS = 90          # publicações mais antigas que isso saem do Discover
META_REFRESH = 2 * DAY  # miniaturas do TikTok expiram: renova quando ficam velhas
PUBLIC_REFRESH_HOURS = 3
DATA_POOL = 30            # quantas faixas vão para a Activity (ela mostra 6 por vez)
BUCKET = "backline"
ACCENT = 0x5865F2


def _md(text: str) -> str:
    return discord.utils.escape_markdown(text).replace("[", "(").replace("]", ")")


def build_layout(tiles: list[Tile], posts: list[dict], header: str, button: discord.ui.Button,
                 view: discord.ui.LayoutView) -> list[discord.File]:
    """Texto do topo, galeria 3×2 (cada quadro já traz título/artista/nível embaixo da capa) e o botão."""
    files: list[discord.File] = []
    items = []
    for t in tiles:
        name = f"track_{t.number}.png"
        files.append(discord.File(io.BytesIO(render_tile(t)), filename=name))
        items.append(discord.MediaGalleryItem(f"attachment://{name}", description=t.title[:100]))
    container = discord.ui.Container(
        discord.ui.TextDisplay(header),
        discord.ui.MediaGallery(*items),
        discord.ui.Separator(),
        discord.ui.ActionRow(button),
        accent_colour=ACCENT)
    view.add_item(container)
    return files


class PublicView(discord.ui.LayoutView):
    """Botão "Open Discover" da grade do canal (persistente): abre a Activity."""

    def __init__(self, bot, cog=None):
        super().__init__(timeout=None)
        self.bot = bot
        self.button = discord.ui.Button(label="Open Discover — tap a track to listen", emoji="🎧",
                                        style=discord.ButtonStyle.primary, custom_id="bl:discover")
        self.button.callback = self.on_click

    async def on_click(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.response.launch_activity()
        except discord.HTTPException:
            await interaction.response.send_message(
                "Discover is warming up — try again in a minute!", ephemeral=True)


class PersonalView(discord.ui.LayoutView):
    def __init__(self, cog: "Discover", user_id: int):
        super().__init__(timeout=3600)
        self.cog, self.user_id = cog, user_id
        self.button = discord.ui.Button(label="New picks", emoji="🔄", style=discord.ButtonStyle.primary)
        self.button.callback = self.on_new

    async def on_new(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self.cog._new_batch(self.user_id)
        view, files = await self.cog.personal(self.user_id)
        await interaction.edit_original_response(view=view, attachments=files)


class Discover(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._covers: OrderedDict[str, bytes | None] = OrderedDict()
        skeleton = PublicView(bot)
        skeleton.add_item(discord.ui.ActionRow(skeleton.button))
        bot.add_view(skeleton)
        self.public_loop.start()

    def cog_unload(self) -> None:
        self.public_loop.cancel()

    async def _avg_received(self, user_id: int) -> float | None:
        row = await self.bot.db.fetchone(
            "SELECT AVG(r.stars) AS a, COUNT(*) AS n FROM ratings r JOIN posts p ON p.id = r.post_id "
            "WHERE p.user_id=?", (user_id,))
        return float(row["a"]) if row and row["n"] else None

    async def _ensure_meta(self, post: dict) -> dict:
        if str(post.get("external_id", "")).startswith("demo-"):
            return post
        stale = not post.get("meta_at") or (post["platform"] == "tiktok" and now() - post["meta_at"] > META_REFRESH)
        if not stale:
            return post
        title, thumb = await self.bot.collectors.post_meta(post["platform"], post["url"], post["external_id"])
        await self.bot.db.execute("UPDATE posts SET title=COALESCE(?, title), thumb_url=?, meta_at=? WHERE id=?",
                                  (title, thumb, now(), post["id"]))
        post.update(title=title or post.get("title"), thumb_url=thumb, meta_at=now())
        return post

    async def candidates(self, viewer_id: int) -> list[Candidate]:
        db, t = self.bot.db, now()
        posts = await db.fetchall(
            "SELECT p.id, p.user_id, p.created_at, s.level, ds.seen_at, b.multiplier, b.until, m.manual_level "
            "FROM posts p JOIN members m ON m.user_id = p.user_id "
            "LEFT JOIN scores s ON s.user_id = p.user_id "
            "LEFT JOIN discover_seen ds ON ds.post_id = p.id AND ds.user_id = ? "
            "LEFT JOIN boosts b ON b.user_id = p.user_id "
            "WHERE p.removed = 0 AND m.verified_at IS NOT NULL AND m.left_at IS NULL AND p.created_at > ?",
            (viewer_id, t - POOL_DAYS * DAY))
        genres: dict[int, set[str]] = {}
        for r in await db.fetchall("SELECT user_id, genre FROM member_genres"):
            genres.setdefault(r["user_id"], set()).add(r["genre"])
        avg: dict[int, float] = {}
        for r in await db.fetchall("SELECT p.user_id, AVG(r.stars) AS a FROM ratings r "
                                   "JOIN posts p ON p.id = r.post_id GROUP BY p.user_id"):
            avg[r["user_id"]] = float(r["a"])
        out = []
        for p in posts:
            boost = p["multiplier"] if p["multiplier"] and (not p["until"] or p["until"] > t) else 1.0
            out.append(Candidate(post_id=p["id"], user_id=p["user_id"], created_at=p["created_at"],
                                 level=p["manual_level"] or p["level"] or 1, genres=genres.get(p["user_id"], set()),
                                 avg_rating=avg.get(p["user_id"]), seen_at=p["seen_at"], boost=float(boost)))
        return out

    # ---------- seleção ----------
    async def _new_batch(self, user_id: int) -> list[int]:
        db, t = self.bot.db, now()
        viewer = Viewer(user_id=user_id,
                        genres={r["genre"] for r in await db.fetchall(
                            "SELECT genre FROM member_genres WHERE user_id=?", (user_id,))},
                        avg_rating=await self._avg_received(user_id))
        chosen = pick(await self.candidates(user_id), viewer, t, n=BATCH, rng=random.Random())
        ids = [c.post_id for c in chosen]
        if ids:
            await db.execute("INSERT INTO discover_batch(user_id, post_ids, created_at) VALUES(?,?,?) "
                             "ON CONFLICT (user_id) DO UPDATE SET post_ids=excluded.post_ids, "
                             "created_at=excluded.created_at", (user_id, ",".join(map(str, ids)), t))
            await db.executemany("INSERT INTO discover_seen(user_id, post_id, seen_at) VALUES(?,?,?) "
                                 "ON CONFLICT (user_id, post_id) DO UPDATE SET seen_at=excluded.seen_at",
                                 [(user_id, i, t) for i in ids])
        return ids

    async def _current(self, user_id: int) -> list[dict]:
        """Seleção pessoal atual (sorteia uma nova se não existir ou se esvaziou)."""
        db = self.bot.db
        row = await db.fetchone("SELECT post_ids FROM discover_batch WHERE user_id=?", (user_id,))
        ids = [int(x) for x in row["post_ids"].split(",") if x] if row else []
        posts = [dict(r) for r in [await db.fetchone("SELECT * FROM posts WHERE id=? AND removed=0", (i,))
                                   for i in ids] if r]
        if len(posts) < min(BATCH, len(ids) or BATCH) or not posts:
            ids = await self._new_batch(user_id)
            posts = [dict(await db.fetchone("SELECT * FROM posts WHERE id=?", (i,))) for i in ids]
        return posts

    async def _cover(self, url: str | None) -> bytes | None:
        if not url:
            return None
        if url in self._covers:
            self._covers.move_to_end(url)
            return self._covers[url]
        data = None
        try:
            async with self.bot.session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status == 200 and int(r.headers.get("Content-Length", "0") or 0) < 5_000_000:
                    data = await r.read()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            data = None
        self._covers[url] = data
        while len(self._covers) > 300:
            self._covers.popitem(last=False)
        return data

    async def _tile(self, n: int, post: dict, opened: bool) -> Tile:
        db, t = self.bot.db, now()
        post = await self._ensure_meta(post)
        m = await db.fetchone("SELECT verified_at, demo_name, manual_level FROM members WHERE user_id=?",
                              (post["user_id"],))
        s = await db.fetchone("SELECT level FROM scores WHERE user_id=?", (post["user_id"],))
        level = (m and m["manual_level"]) or (s and s["level"]) or 1
        genres = [r["genre"] for r in await db.fetchall("SELECT genre FROM member_genres WHERE user_id=?",
                                                         (post["user_id"],))]
        if m and m["demo_name"]:
            artist = m["demo_name"]
        else:
            user = self.bot.get_user(post["user_id"])
            artist = user.display_name if user else "Backline artist"
        new = m and m["verified_at"] and t - m["verified_at"] < NEW_ARTIST_DAYS * DAY
        return Tile(number=n, title=post.get("title") or f"{PLATFORM_NAME[post['platform']]} post",
                    artist=artist, genre=" · ".join(sorted(genres)) or "Music",
                    badge="New artist" if new else f"Level {level}", platform=post["platform"],
                    cover=await self._cover(post.get("thumb_url")), opened=opened)

    async def _tiles(self, posts: list[dict]) -> list[Tile]:
        posts[:] = [await self._ensure_meta(p) for p in posts]
        return list(await asyncio.gather(*(self._tile(i, p, False) for i, p in enumerate(posts, start=1))))

    async def personal(self, user_id: int) -> tuple[discord.ui.LayoutView, list[discord.File]]:
        posts = await self._current(user_id)
        view = PersonalView(self, user_id)
        if not posts:
            view.add_item(discord.ui.Container(discord.ui.TextDisplay(
                "## 🎧 Picks for you\nNothing to discover yet — new tracks are on the way. Check back soon!"),
                accent_colour=ACCENT))
            return view, []
        tiles = await self._tiles(posts)
        header = ("## 🎧 Picks for you\n"
                  f"{len(posts)} tracks chosen for you, based on the genres you like, what's new and the "
                  "artists who support the community the most.\n"
                  "Tap **▶ number** below the grid to listen. Want different ones? Press **New picks**.")
        files = await asyncio.get_running_loop().run_in_executor(
            None, lambda: build_layout(tiles, posts, header, view.button, view))
        return view, files

    async def public_posts(self) -> list[dict]:
        db = self.bot.db
        chosen = pick(await self.candidates(0), Viewer(user_id=0, genres=set()), now(), n=BATCH,
                      rng=random.Random())
        return [dict(await db.fetchone("SELECT * FROM posts WHERE id=?", (c.post_id,))) for c in chosen]

    async def refresh_public(self) -> None:
        """Grade pública do #discover: aparece sem clique e troca sozinha a cada poucas horas."""
        db = self.bot.db
        channel = await self.bot.channel("discover")
        if not isinstance(channel, discord.TextChannel):
            return
        # painel antigo (versão com botão Discover) sai
        old = await db.get_int_setting("msg_discover")
        if old:
            try:
                await channel.get_partial_message(old).delete()
            except discord.HTTPException:
                pass
            await db.execute("DELETE FROM settings WHERE key='msg_discover'")
        posts = await self.public_posts()
        view = PublicView(self.bot)
        header = ("# 🧭 Discover\n"
                  "Fresh tracks from independent artists in the Backline community.\n"
                  "Press **Open Discover** and tap any track to listen — then leave some love: "
                  "watch, like, comment, follow.")
        if posts:
            tiles = await self._tiles(posts)
            files = await asyncio.get_running_loop().run_in_executor(
                None, lambda: build_layout(tiles, posts, header, view.button, view))
        else:
            files = []
            view.add_item(discord.ui.Container(discord.ui.TextDisplay(
                header + "\n\n*No tracks yet — be the first to share yours in the post channel!*"),
                discord.ui.ActionRow(view.button), accent_colour=ACCENT))
        try:
            await self.publish_data()
        except Exception as e:  # noqa: BLE001
            await self.bot.log_admin(f"⚠️ Dados do Discover (Activity) não publicados: `{str(e)[:300]}`")
        mid = await db.get_int_setting("msg_discover_public")
        if mid:
            try:
                await channel.get_partial_message(mid).edit(view=view, attachments=files)
                return
            except discord.HTTPException:
                pass
        msg = await channel.send(view=view, files=files)
        await db.set_setting("msg_discover_public", msg.id)

    # ---------- dados da Activity (Supabase Storage público) ----------
    def _supabase(self) -> tuple[str, dict]:
        import json as _json
        cfg = _json.load(open(self.bot.cfg.supabase_config))
        key = cfg["supabaseChave"]
        return cfg["supabaseUrl"].rstrip("/"), {"apikey": key, "Authorization": f"Bearer {key}"}

    async def _upload(self, base: str, headers: dict, path: str, data: bytes, ctype: str) -> None:
        h = dict(headers, **{"x-upsert": "true", "Content-Type": ctype, "cache-control": "max-age=60"})
        async with self.bot.session.post(f"{base}/storage/v1/object/{BUCKET}/{path}", data=data, headers=h) as r:
            if r.status >= 300:
                raise RuntimeError(f"upload {path}: {r.status} {(await r.text())[:150]}")

    async def publish_data(self) -> None:
        import json as _json
        from PIL import Image
        base, headers = self._supabase()
        async with self.bot.session.post(f"{base}/storage/v1/bucket", headers=headers,
                                         json={"id": BUCKET, "name": BUCKET, "public": True}) as r:
            if r.status >= 300 and r.status not in (400, 409):
                raise RuntimeError(f"bucket: {r.status} {(await r.text())[:150]}")
        db = self.bot.db
        chosen = pick(await self.candidates(0), Viewer(user_id=0, genres=set()), now(), n=DATA_POOL,
                      rng=random.Random())
        posts = [dict(await db.fetchone("SELECT * FROM posts WHERE id=?", (c.post_id,))) for c in chosen]
        tiles = await self._tiles(posts)
        out = []
        for t, p in zip(tiles, posts):
            cover = None
            if t.cover:
                try:
                    img = Image.open(io.BytesIO(t.cover)).convert("RGB")
                    img.thumbnail((720, 720))
                    buf = io.BytesIO()
                    img.save(buf, "JPEG", quality=85)
                    cover = f"covers/{p['id']}.jpg"
                    await self._upload(base, headers, cover, buf.getvalue(), "image/jpeg")
                except Exception:  # noqa: BLE001
                    cover = None
            out.append({"id": p["id"], "title": t.title, "artist": t.artist, "genre": t.genre, "badge": t.badge,
                        "new": t.badge.lower().startswith("new"), "platform": p["platform"],
                        "platform_name": PLATFORM_NAME[p["platform"]], "url": p["url"], "cover": cover})
        payload = _json.dumps({"updated_at": now(), "tracks": out}).encode()
        await self._upload(base, headers, "discover.json", payload, "application/json")

    @tasks.loop(minutes=30)
    async def public_loop(self) -> None:
        try:
            await self.refresh_public()
        except Exception as e:  # noqa: BLE001
            await self.bot.log_admin(f"⚠️ Discover público não atualizou: `{str(e)[:300]}`")

    @public_loop.before_loop
    async def _ready(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(5)


async def setup(bot):
    await bot.add_cog(Discover(bot))
