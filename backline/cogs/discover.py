"""#discover: grade com 6 trabalhos da comunidade escolhidos para cada membro.

Funcionamento: o membro clica em Discover e recebe uma imagem 3×2 com 6 sugestões e 6 botões numerados.
Cada botão abre o link da publicação (e marca como aberta). Quando as 6 forem abertas, a grade
troca sozinha pelas próximas 6. A seleção fica guardada: fechar e voltar não sorteia de novo.
"""
from __future__ import annotations

import asyncio
import random
from collections import OrderedDict

import aiohttp
import discord
from discord.ext import commands

from ..db import now
from ..discover import DAY, NEW_ARTIST_DAYS, Candidate, Viewer, pick
from ..grid import Tile, render
from ..links import PLATFORM_EMOJI, PLATFORM_NAME

BATCH = 6
POOL_DAYS = 90          # publicações mais antigas que isso saem do Discover
META_REFRESH = 2 * DAY  # miniaturas do TikTok expiram: renova quando ficam velhas
ACCENT = 0x5865F2


def panel_embed() -> discord.Embed:
    return discord.Embed(
        title="🧭 Discover",
        description=("Find your next favorite independent artist.\n\n"
                     "Press **Discover** to get **6 tracks picked for you** from the community — "
                     "based on the genres you like, what's new, and the artists who support others the most.\n\n"
                     "Open all 6 and the next 6 unlock automatically."),
        colour=ACCENT)


class DiscoverPanelView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot
        b = discord.ui.Button(label="Discover", emoji="🎧", style=discord.ButtonStyle.primary, custom_id="bl:discover")
        b.callback = self.on_discover
        self.add_item(b)

    async def on_discover(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        embed, file, view = await self.bot.get_cog("Discover").screen(interaction.user.id)
        kwargs = {"file": file} if file else {}
        await interaction.followup.send(embed=embed, view=view, ephemeral=True, **kwargs)


class GridView(discord.ui.View):
    def __init__(self, cog: "Discover", user_id: int, posts: list[dict], opened: set[int]):
        super().__init__(timeout=3600)
        self.cog, self.user_id = cog, user_id
        for i, p in enumerate(posts, start=1):
            done = p["id"] in opened
            btn = discord.ui.Button(label=str(i), emoji="✅" if done else PLATFORM_EMOJI[p["platform"]],
                                    style=discord.ButtonStyle.success if done else discord.ButtonStyle.secondary,
                                    row=(i - 1) // 3)
            btn.callback = self._make_cb(p)
            self.add_item(btn)

    def _make_cb(self, post: dict):
        async def cb(interaction: discord.Interaction) -> None:
            await self.cog.open_track(interaction, self.user_id, post)
        return cb


class Discover(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._covers: OrderedDict[str, bytes | None] = OrderedDict()
        bot.add_view(DiscoverPanelView(bot))

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

    async def _current(self, user_id: int) -> tuple[list[dict], set[int]]:
        """Seleção atual (posts ainda válidos) e quais já foram abertos. Sorteia nova se acabou."""
        db = self.bot.db
        row = await db.fetchone("SELECT post_ids, created_at FROM discover_batch WHERE user_id=?", (user_id,))
        ids = [int(x) for x in row["post_ids"].split(",") if x] if row else []
        posts = [dict(r) for r in [await db.fetchone("SELECT * FROM posts WHERE id=? AND removed=0", (i,))
                                   for i in ids] if r]
        opened = {r["post_id"] for r in await db.fetchall(
            "SELECT post_id FROM discover_clicks WHERE user_id=? AND clicked_at >= ?",
            (user_id, row["created_at"] if row else 0))}
        if not posts or all(p["id"] in opened for p in posts):
            ids = await self._new_batch(user_id)
            posts = [dict(await db.fetchone("SELECT * FROM posts WHERE id=?", (i,))) for i in ids]
            opened = set()
        return posts, opened

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

    async def screen(self, user_id: int) -> tuple[discord.Embed, discord.File | None, discord.ui.View]:
        posts, opened = await self._current(user_id)
        if not posts:
            embed = discord.Embed(title="🧭 Discover", colour=ACCENT,
                                  description="Nothing to discover yet — new tracks are on the way. Check back soon!")
            return embed, None, discord.ui.View()
        tiles = await asyncio.gather(*(self._tile(i, p, p["id"] in opened) for i, p in enumerate(posts, start=1)))
        png = await asyncio.get_running_loop().run_in_executor(None, render, list(tiles))
        n_open = sum(1 for p in posts if p["id"] in opened)
        embed = discord.Embed(
            title="🧭 Discover",
            description=(f"**{len(posts)} tracks picked for you** from the Backline community.\n\n"
                         "**How it works**\n"
                         "Tap a number below to open that track — it's marked as opened.\n"
                         f"Open all {len(posts)} and your **next {BATCH} unlock automatically**.\n\n"
                         f"Progress: **{n_open}/{len(posts)}** opened"),
            colour=ACCENT)
        embed.set_image(url="attachment://discover.png")
        file = discord.File(__import__("io").BytesIO(png), filename="discover.png")
        return embed, file, GridView(self, user_id, posts, opened)

    async def open_track(self, interaction: discord.Interaction, user_id: int, post: dict) -> None:
        db = self.bot.db
        await db.execute("INSERT INTO discover_clicks(user_id, post_id, clicked_at) VALUES(?,?,?) "
                         "ON CONFLICT (user_id, post_id) DO UPDATE SET clicked_at=excluded.clicked_at",
                         (user_id, post["id"], now()))
        link = discord.ui.View()
        link.add_item(discord.ui.Button(label=f"Open on {PLATFORM_NAME[post['platform']]}", url=post["url"],
                                        emoji=PLATFORM_EMOJI[post["platform"]]))
        title = post.get("title") or f"{PLATFORM_NAME[post['platform']]} post"
        await interaction.response.send_message(f"▶️ **{title}**", view=link, ephemeral=True)
        embed, file, view = await self.screen(user_id)
        try:
            kwargs = {"attachments": [file]} if file else {"attachments": []}
            await interaction.followup.edit_message(interaction.message.id, embed=embed, view=view, **kwargs)
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(Discover(bot))
