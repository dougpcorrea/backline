"""Feed: membros publicam o link do trabalho, o bot cria o cartão no #feed."""
from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from ..collectors import CollectorError
from ..db import now
from ..i18n import bi_short, lang_of, t
from ..links import PLATFORM_EMOJI, PLATFORM_NAME, is_short_tiktok, parse_post_url
from ..scoring import level_color
from ..services import posts_in_last_week, post_member_commenters

WEEK = 7 * 86400


class PostPanelView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot
        btn = discord.ui.Button(label=bi_short("btn_new_post"), style=discord.ButtonStyle.primary,
                                emoji="📤", custom_id="bl:newpost")
        btn.callback = self.on_click
        self.add_item(btn)

    async def on_click(self, interaction: discord.Interaction) -> None:
        await open_post_modal(self.bot, interaction)


class PostModal(discord.ui.Modal):
    def __init__(self, bot, lang: str, member_genres: list[str] | None = None):
        super().__init__(title=t("modal_post", lang), timeout=900)
        self.bot, self.lang = bot, lang
        self.link = discord.ui.TextInput(max_length=300, placeholder="https://www.instagram.com/reel/...")
        self.title_in = discord.ui.TextInput(max_length=80, required=False,
                                             placeholder="Leave empty to use the title from the post")
        self.kind = discord.ui.Select(placeholder="Original song or cover?", min_values=1, max_values=1, options=[
            discord.SelectOption(label="Original", value="original", emoji="✍️", description="Your own song"),
            discord.SelectOption(label="Cover", value="cover", emoji="🎤", description="Your version of someone's song"),
        ])
        mg = set(member_genres or [])
        self.genres = discord.ui.Select(placeholder="Pick 1–3 genres", min_values=1, max_values=3, options=[
            discord.SelectOption(label=g, value=g, default=g in mg) for g in bot.cfg.genres[:25]])
        self.add_item(discord.ui.Label(text="Post link", description="Instagram or YouTube",
                                       component=self.link))
        self.add_item(discord.ui.Label(text="Title (optional)", component=self.title_in))
        self.add_item(discord.ui.Label(text="Original or cover?", component=self.kind))
        self.add_item(discord.ui.Label(text="Genres", component=self.genres))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        msg = await self.bot.get_cog("Feed").submit(
            interaction.user, self.link.value, self.lang, title=self.title_in.value.strip() or None,
            kind=(self.kind.values or ["original"])[0], genres=list(self.genres.values))
        await interaction.followup.send(msg, ephemeral=True)


async def open_post_modal(bot, interaction: discord.Interaction) -> None:
    genres = [r["genre"] for r in await bot.db.fetchall(
        "SELECT genre FROM member_genres WHERE user_id=?", (interaction.user.id,))]
    await interaction.response.send_modal(PostModal(bot, lang_of(interaction.locale), genres))


class Feed(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        bot.add_view(PostPanelView(bot))

    @app_commands.command(name="post", description="Share a new work in the feed")
    @app_commands.guild_only()
    async def post_cmd(self, interaction: discord.Interaction) -> None:
        await open_post_modal(self.bot, interaction)

    async def submit(self, user: discord.abc.User, raw_url: str, lang: str, title: str | None = None,
                     kind: str = "original", genres: list[str] | None = None) -> str:
        bot, db = self.bot, self.bot.db
        member = await db.get_member(user.id)
        if not member or not member["verified_at"]:
            return t("err_not_verified", lang, channel=await bot.channel_mention("start"))

        url = raw_url.strip()
        if is_short_tiktok(url):
            url = await bot.collectors.resolve_redirect(url if "://" in url else "https://" + url)
        link = parse_post_url(url)
        if not link:
            return ("❌ That doesn't look like a post link. Paste the link of a specific **Instagram post/reel** "
                    "or **YouTube video/short** — not a profile or channel link.")
        if link.platform not in ("instagram", "youtube"):
            return "❌ Backline works with **Instagram** and **YouTube** posts only."
        tester = bool(member["is_test"]) and not member["demo_name"]

        handle = await db.fetchone("SELECT * FROM handles WHERE user_id=? AND platform=? AND verified=1",
                                   (user.id, link.platform))
        if not handle:
            return t("err_platform_not_verified", lang, platform=PLATFORM_NAME[link.platform],
                     channel=await bot.channel_mention("start"))

        # Confere se a publicação é do próprio membro (quando a plataforma permite)
        if link.platform == "tiktok" and link.owner_hint and link.owner_hint != handle["handle"]:
            return t("err_not_owner", lang, platform="TikTok", handle=handle["handle"])
        if link.platform == "youtube" and handle["external_id"]:
            try:
                channel_id = await bot.collectors.youtube_video_channel(link.external_id)
            except CollectorError:
                channel_id = handle["external_id"]  # API fora do ar: não bloqueia o membro
            if channel_id != handle["external_id"]:
                return t("err_not_owner", lang, platform="YouTube", handle=handle["handle"])

        if await db.fetchone("SELECT id FROM posts WHERE platform=? AND external_id=?",
                             (link.platform, link.external_id)):
            return t("err_duplicate", lang)

        # a publicação existe mesmo? (e, no Instagram, é da conta verificada?)
        details = {"found": None, "title": None, "thumb": None, "owner": None}
        if not tester:
            details = await bot.collectors.post_details(link.platform, link.url, link.external_id)
            if details["found"] is False:
                return (f"❌ I couldn't find that {PLATFORM_NAME[link.platform]} post. Check the link and make sure "
                        "the post is **public**.")
            if (link.platform in ("instagram", "tiktok") and details["owner"]
                    and details["owner"] != handle["handle"].lower()):
                return t("err_not_owner", lang, platform=PLATFORM_NAME[link.platform], handle=handle["handle"])

        recent = await posts_in_last_week(db, user.id)
        limit = bot.cfg.posts_per_week
        if len(recent) >= limit:
            free_at = recent[0]["created_at"] + WEEK
            return t("err_limit", lang, n=limit, when=f"<t:{free_at}:R>")

        valid = [g for g in (genres or []) if g in bot.cfg.genres][:3]
        post_id = await db.insert_returning_id(
            "INSERT INTO posts(user_id, platform, url, external_id, created_at, title, thumb_url, meta_at, kind, genres) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (user.id, link.platform, link.url, link.external_id, now(), title or details["title"],
             details["thumb"], now() if details["found"] else None,
             kind if kind in ("original", "cover") else "original", ",".join(valid) or None))

        disc = bot.get_cog("Discover")
        if disc:
            import asyncio
            asyncio.create_task(disc.publish_data())   # já entra no app do Discover
        lvl = await db.fetchone("SELECT s.level, m.manual_level FROM members m LEFT JOIN scores s "
                                "ON s.user_id=m.user_id WHERE m.user_id=?", (user.id,))
        level = (lvl and (lvl["manual_level"] or lvl["level"])) or 0
        from .discover import level_rule_active
        if level < 3 and await level_rule_active(db):
            return ("✅ Your post is saved! It will show up in Discover as soon as you reach **Lv.3** — "
                    "comment on other members' posts to get there (the bot checks every 30 minutes).")
        return t("post_ok", lang, channel=await bot.channel_mention("discover"))

    async def card(self, post_id: int) -> tuple[discord.Embed, discord.ui.View]:
        db = self.bot.db
        post = await db.fetchone("SELECT * FROM posts WHERE id=?", (post_id,))
        score = await db.fetchone("SELECT level FROM scores WHERE user_id=?", (post["user_id"],))
        level = score["level"] if score else 1
        genres = [r["genre"] for r in await db.fetchall(
            "SELECT genre FROM member_genres WHERE user_id=?", (post["user_id"],))]
        n = len(await post_member_commenters(db, post_id))
        demo = await db.fetchone("SELECT demo_name FROM members WHERE user_id=?", (post["user_id"],))
        who = f"**{demo['demo_name']}** (demo)" if demo and demo["demo_name"] else f"<@{post['user_id']}>"

        platform = post["platform"]
        embed = discord.Embed(
            title=f"{PLATFORM_EMOJI[platform]} {post['title'] or t('card_new', 'en')}"[:256],
            description=f"{who} · {PLATFORM_NAME[platform]}\n{post['url']}",
            colour=level_color(level),
            timestamp=datetime.fromtimestamp(post["created_at"], tz=timezone.utc),
        )
        if genres:
            embed.add_field(name="🎵", value=" · ".join(genres), inline=True)
        embed.add_field(name="💬", value=f"{t('card_comments', 'en', n=n)}",
                        inline=True)
        if post["thumb_url"]:
            embed.set_thumbnail(url=post["thumb_url"])
        embed.set_footer(text=f"#{post_id}")
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label=bi_short("btn_open"), url=post["url"], emoji="🔗"))
        return embed, view

    async def refresh_card(self, post_id: int) -> None:
        post = await self.bot.db.fetchone("SELECT * FROM posts WHERE id=?", (post_id,))
        if not post or not post["channel_id"] or not post["message_id"]:
            return
        channel = self.bot.get_channel(post["channel_id"])
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            msg = channel.get_partial_message(post["message_id"])
            if post["removed"]:
                await msg.delete()
                return
            embed, view = await self.card(post_id)
            await msg.edit(embed=embed, view=view)
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(Feed(bot))
