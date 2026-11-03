"""Artistas e publicações fictícias para ver o Feed e o Discover funcionando.

São criados uma única vez (ao iniciar), aparecem marcados como "(demo)" e saem com /demo clear.
Não têm conta no Discord: ids altos fixos, nome guardado em members.demo_name.
Links apontam para example.com e as imagens são fotos genéricas (picsum.photos).
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..db import now

DEMO_BASE_ID = 900_000_000_000_000_000
DAY = 86400

# nome, gêneros, nível, data de verificação (dias atrás), publicações (título, plataforma, dias atrás)
ARTISTS = [
    ("Luna Vale", ["Pop", "R&B / Soul"], 8, 120, [("Late Night Drive", "youtube", 2), ("Glass Hearts", "tiktok", 12)]),
    ("The Velvet Rooms", ["Indie / Alternative", "Rock"], 6, 200, [("Blue Room", "youtube", 4)]),
    ("Kai Moreno", ["Latin", "Pop"], 9, 90, [("Olhos nos Olhos (cover)", "instagram", 1), ("Saudade FM", "tiktok", 9)]),
    ("Maya Brooks", ["Folk / Acoustic"], 3, 5, [("Somewhere", "youtube", 3)]),
    ("DJ Northbeam", ["Electronic"], 5, 60, [("Neon Tides", "tiktok", 6), ("Afterglow Mix", "youtube", 20)]),
    ("Theo Grant Trio", ["Jazz / Blues"], 10, 300, [("Autumn Standards", "youtube", 5)]),
    ("Rhyme Theory", ["Hip-Hop / Rap"], 2, 3, [("First Verse", "instagram", 2)]),
    ("Clara Sun", ["Gospel / Christian", "R&B / Soul"], 7, 150, [("Morning Light", "tiktok", 7)]),
]


# Vitrine: publicações reais (links de verdade) de artistas convidados, no lugar das fictícias.
# Ficam como membros "demo" (sem conta no Discord), não entram na coleta de comentários nem no ranking.
SHOWCASE_BASE = DEMO_BASE_ID + 100
SHOWCASE = [
    ("https://www.youtube.com/shorts/2C3Hrr1U0Ok", "Latin"),
    ("https://www.instagram.com/p/DfPRhSEu77g/", "Rock"),
    ("https://www.instagram.com/reel/DVAYfL6jX0_/", "Hip-Hop / Rap"),
    ("https://www.youtube.com/shorts/N_26BEzlSOM", "Pop"),
    ("https://www.instagram.com/p/DfQBl7gBSpS/", "Hip-Hop / Rap"),
]


class Demo(commands.Cog):
    demo = app_commands.Group(name="demo", description="Dados de demonstração",
                              default_permissions=discord.Permissions(administrator=True), guild_only=True)

    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if await self.bot.db.get_setting("demo_seeded"):
            return
        await self.bot.db.set_setting("demo_seeded", "1")
        n = await self.seed()
        await self.bot.log_admin(f"🧪 {n} publicações de demonstração criadas (artistas fictícios). "
                                 "Para apagar: `/demo clear`.")

    async def seed(self) -> int:
        db, t = self.bot.db, now()
        count = 0
        for i, (name, genres, level, verified_days, posts) in enumerate(ARTISTS, start=1):
            uid = DEMO_BASE_ID + i
            await db.execute("INSERT INTO members(user_id, locale, joined_at, verified_at, manual_level, is_test, demo_name) "
                             "VALUES(?,?,?,?,?,1,?) ON CONFLICT (user_id) DO NOTHING",
                             (uid, "en", t - verified_days * DAY, t - verified_days * DAY, level, name))
            await db.execute("INSERT INTO scores(user_id, level, updated_at) VALUES(?,?,?) ON CONFLICT (user_id) "
                             "DO UPDATE SET level=excluded.level", (uid, level, t))
            await db.executemany("INSERT INTO member_genres(user_id, genre) VALUES(?,?) ON CONFLICT DO NOTHING",
                                 [(uid, g) for g in genres])
            for j, (title, platform, days) in enumerate(posts, start=1):
                ext = f"demo-{i}-{j}"
                if await db.fetchone("SELECT id FROM posts WHERE platform=? AND external_id=?", (platform, ext)):
                    continue
                await db.insert_returning_id(
                    "INSERT INTO posts(user_id, platform, url, external_id, created_at, checkpoints_done, "
                    "title, thumb_url, meta_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (uid, platform, f"https://example.com/backline-demo/{ext}", ext, t - days * DAY, 99,
                     title, f"https://picsum.photos/seed/backline-{ext}/480/480", t))
                count += 1
        return count

    async def seed_showcase(self) -> int:
        from ..links import parse_post_url
        db, t, count = self.bot.db, now(), 0
        for i, (url, genre) in enumerate(SHOWCASE, start=1):
            link = parse_post_url(url)
            if not link or await db.fetchone("SELECT id FROM posts WHERE platform=? AND external_id=?",
                                             (link.platform, link.external_id)):
                continue
            d = await self.bot.collectors.post_details(link.platform, link.url, link.external_id)
            uid = SHOWCASE_BASE + i
            name = f"@{d['owner']}" if d.get("owner") else f"Guest artist {i}"
            await db.execute("INSERT INTO members(user_id, locale, joined_at, verified_at, manual_level, is_test, "
                             "demo_name) VALUES(?,?,?,?,?,1,?) ON CONFLICT (user_id) DO UPDATE SET "
                             "demo_name=excluded.demo_name", (uid, "en", t, t, 5, name))
            await db.execute("INSERT INTO scores(user_id, level, updated_at) VALUES(?,?,?) ON CONFLICT (user_id) "
                             "DO UPDATE SET level=excluded.level", (uid, 5, t))
            await db.execute("INSERT INTO member_genres(user_id, genre) VALUES(?,?) ON CONFLICT DO NOTHING",
                             (uid, genre))
            await db.insert_returning_id(
                "INSERT INTO posts(user_id, platform, url, external_id, created_at, checkpoints_done, title, "
                "thumb_url, meta_at, kind, genres) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (uid, link.platform, link.url, link.external_id, t - i * 3600, 99, d.get("title"),
                 d.get("thumb"), t if d.get("found") else None, "original", genre))
            count += 1
        return count

    async def clear(self, showcase: bool = True) -> int:
        db = self.bot.db
        rows = await db.fetchall("SELECT p.id, p.channel_id, p.message_id FROM posts p "
                                 "JOIN members m ON m.user_id = p.user_id WHERE m.demo_name IS NOT NULL "
                                 + ("" if showcase else f"AND p.user_id < {SHOWCASE_BASE}"))
        for r in rows:
            ch = self.bot.get_channel(r["channel_id"]) if r["channel_id"] else None
            if isinstance(ch, discord.TextChannel) and r["message_id"]:
                try:
                    await ch.get_partial_message(r["message_id"]).delete()
                except discord.HTTPException:
                    pass
        ids = [DEMO_BASE_ID + i for i in range(1, len(ARTISTS) + 1)]
        if showcase:
            ids += [SHOWCASE_BASE + i for i in range(1, len(SHOWCASE) + 1)]
        for uid in ids:
            for sql in ("DELETE FROM discover_seen WHERE post_id IN (SELECT id FROM posts WHERE user_id=?)",
                        "DELETE FROM saves WHERE post_id IN (SELECT id FROM posts WHERE user_id=?)",
                        "DELETE FROM ratings WHERE post_id IN (SELECT id FROM posts WHERE user_id=?)",
                        "DELETE FROM posts WHERE user_id=?", "DELETE FROM member_genres WHERE user_id=?",
                        "DELETE FROM scores WHERE user_id=?", "DELETE FROM members WHERE user_id=?"):
                await db.execute(sql, (uid,))
        return len(rows)

    @demo.command(name="clear", description="Apagar os artistas e publicações de demonstração")
    async def clear_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        n = await self.clear()
        await interaction.followup.send(f"🧹 {n} publicações de demonstração apagadas.", ephemeral=True)

    @demo.command(name="seed", description="Recriar os artistas e publicações de demonstração")
    async def seed_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        n = await self.seed()
        await interaction.followup.send(f"🧪 {n} publicações de demonstração criadas.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Demo(bot))
