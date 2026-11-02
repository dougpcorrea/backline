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
        await interaction.response.send_modal(PostModal(self.bot, lang_of(interaction.locale)))


class PostModal(discord.ui.Modal):
    def __init__(self, bot, lang: str):
        super().__init__(title=t("modal_post", lang), timeout=600)
        self.bot, self.lang = bot, lang
        self.link = discord.ui.TextInput(label=t("field_link", lang), max_length=300,
                                         placeholder="https://www.instagram.com/reel/...")
        self.add_item(self.link)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        msg = await self.bot.get_cog("Feed").submit(interaction.user, self.link.value, self.lang)
        await interaction.followup.send(msg, ephemeral=True)


class Feed(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        bot.add_view(PostPanelView(bot))

    @app_commands.command(name="post", description="Share a new work in the feed")
    @app_commands.guild_only()
    async def post_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(PostModal(self.bot, lang_of(interaction.locale)))

    async def submit(self, user: discord.abc.User, raw_url: str, lang: str) -> str:
        bot, db = self.bot, self.bot.db
        member = await db.get_member(user.id)
        if not member or not member["verified_at"]:
            return t("err_not_verified", lang, channel=await bot.channel_mention("start"))

        url = raw_url.strip()
        if is_short_tiktok(url):
            url = await bot.collectors.resolve_redirect(url if "://" in url else "https://" + url)
        link = parse_post_url(url)
        if not link:
            return t("err_bad_link", lang)

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

        recent = await posts_in_last_week(db, user.id)
        limit = bot.cfg.posts_per_week
        if len(recent) >= limit:
            free_at = recent[0]["created_at"] + WEEK
            return t("err_limit", lang, n=limit, when=f"<t:{free_at}:R>")

        post_id = await db.insert_returning_id(
            "INSERT INTO posts(user_id, platform, url, external_id, created_at) VALUES(?,?,?,?,?)",
            (user.id, link.platform, link.url, link.external_id, now()))
        title, thumb = await bot.collectors.post_meta(link.platform, link.url, link.external_id)
        await db.execute("UPDATE posts SET title=?, thumb_url=?, meta_at=? WHERE id=?",
                         (title, thumb, now(), post_id))

        disc = bot.get_cog("Discover")
        if disc:
            import asyncio
            asyncio.create_task(disc.publish_data())   # já entra no app do Discover
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
