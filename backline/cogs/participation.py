"""Participação: coleta de comentários, índice de reciprocidade, cargos de nível e ranking."""
from __future__ import annotations

import asyncio

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..collectors import CollectorError
from ..db import now
from ..i18n import lang_of, t
from ..links import PLATFORM_NAME
from ..scoring import level_color, progress_bar
from ..services import (compute_scores, posts_due_for_collection, recruiter_stats, record_comments)


class Participation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._lock = asyncio.Lock()
        self.collect_loop.start()
        self.recalc_loop.start()

    def cog_unload(self) -> None:
        self.collect_loop.cancel()
        self.recalc_loop.cancel()

    # ================= coleta =================
    async def collect_post(self, post, advance_checkpoint: bool = True) -> int:
        """Coleta os comentários de uma publicação. Devolve quantos membros novos comentaram."""
        bot, db = self.bot, self.bot.db
        try:
            comments = await bot.collectors.comments(post["platform"], post["external_id"], post["url"])
        except CollectorError as e:
            await db.execute("INSERT INTO collection_log(post_id, at, ok, found, error) VALUES(?,?,0,0,?)",
                             (post["id"], now(), str(e)[:500]))
            await bot.log_admin(f"⚠️ Coleta falhou na publicação #{post['id']} ({post['platform']}): {e}")
            return 0
        new_ids = await record_comments(db, post["id"], post["platform"], comments)
        sets = "last_collected=?" + (", checkpoints_done=checkpoints_done+1" if advance_checkpoint else "")
        await db.execute(f"UPDATE posts SET {sets} WHERE id=?", (now(), post["id"]))
        await db.execute("INSERT INTO collection_log(post_id, at, ok, found) VALUES(?,?,1,?)",
                         (post["id"], now(), len(comments)))
        feed = bot.get_cog("Feed")
        if feed:
            await feed.refresh_card(post["id"])
        if new_ids:
            await self.notify_author(post, new_ids)
        return len(new_ids)

    async def notify_author(self, post, commenter_ids: list[int]) -> None:
        guild = self.bot.guild
        author = guild.get_member(post["user_id"]) if guild else None
        if not author:
            return
        m = await self.bot.db.get_member(post["user_id"])
        lang = m["locale"] if m else "en"
        names = ", ".join(f"<@{uid}>" for uid in commenter_ids[:15])
        if len(commenter_ids) > 15:
            names += f" +{len(commenter_ids) - 15}"
        try:
            await author.send(t("dm_new_comments", lang, names=names, url=post["url"]))
        except discord.HTTPException:
            pass  # DMs fechadas

    @tasks.loop(minutes=15)
    async def collect_loop(self) -> None:
        if await self.bot.is_paused():
            return
        async with self._lock:
            due = await posts_due_for_collection(self.bot.db, self.bot.cfg.checkpoints_hours)
            for post in due:
                await self.collect_post(post)
            if due:
                await self._recalc()

    @collect_loop.before_loop
    async def _wait_ready_collect(self) -> None:
        await self.bot.wait_until_ready()

    # ================= índice e cargos =================
    @tasks.loop(hours=6)
    async def recalc_loop(self) -> None:
        if await self.bot.is_paused():
            return
        async with self._lock:
            await self._recalc()

    @recalc_loop.before_loop
    async def _wait_ready_recalc(self) -> None:
        await self.bot.wait_until_ready()

    async def recalc(self) -> int:
        async with self._lock:
            return await self._recalc()

    async def _recalc(self) -> int:
        cfg = self.bot.cfg
        scores = await compute_scores(self.bot.db, cfg.window_days, cfg.min_activity, cfg.target_ratio)
        levels = {uid: s.level for uid, s in scores.items()}
        for r in await self.bot.db.fetchall("SELECT user_id, manual_level FROM members "
                                            "WHERE manual_level IS NOT NULL AND left_at IS NULL"):
            levels[r["user_id"]] = r["manual_level"]
        changed = await self.sync_level_roles(levels)
        await self.sync_recruiter_roles()
        await self.update_ranking_message()
        return changed

    async def sync_level_roles(self, levels: dict[int, int]) -> int:
        guild = self.bot.guild
        if not guild:
            return 0
        roles = {n: await self.bot.role(f"level_{n}") for n in range(1, 11)}
        if not all(roles.values()):
            return 0
        changed = 0
        for uid, level in levels.items():
            member = guild.get_member(uid)
            if not member:
                continue
            want = roles[level]
            wrong = [r for n, r in roles.items() if n != level and r in member.roles]
            try:
                if wrong:
                    await member.remove_roles(*wrong, reason="Backline level")
                if want not in member.roles:
                    await member.add_roles(want, reason="Backline level")
                    changed += 1
            except discord.HTTPException as e:
                await self.bot.log_admin(f"⚠️ Cargo de nível de {member.mention}: {e}")
        return changed

    async def sync_recruiter_roles(self) -> None:
        guild = self.bot.guild
        role = await self.bot.role("recruiter")
        if not guild or not role:
            return
        stats = await recruiter_stats(self.bot.db, self.bot.cfg.min_activity)
        for uid, st in stats.items():
            member = guild.get_member(uid)
            # O cargo é conquistado e não é retirado automaticamente
            if member and st.active >= self.bot.cfg.recruiter_min_active and role not in member.roles:
                try:
                    await member.add_roles(role, reason="Backline recruiter")
                    await self.bot.log_admin(f"🛡️ {member.mention} virou Divulgador ({st.active} recrutados ativos)")
                except discord.HTTPException:
                    pass

    # ================= ranking =================
    async def ranking_embed(self) -> discord.Embed:
        db, cfg = self.bot.db, self.bot.cfg
        top = await db.fetchall(
            "SELECT s.* FROM scores s JOIN members m ON m.user_id=s.user_id "
            "WHERE m.left_at IS NULL AND s.given > 0 ORDER BY s.level DESC, s.given DESC LIMIT 10")
        medals = ["🥇", "🥈", "🥉"] + [f"`{i}.`" for i in range(4, 11)]
        part = "\n".join(
            f"{medals[i]} <@{r['user_id']}> — Lv.{r['level']} · {r['given']}💬" for i, r in enumerate(top)
        ) or f"{t('ranking_empty', 'en')}"

        stats = sorted((await recruiter_stats(db, cfg.min_activity)).values(), key=lambda s: -s.score)[:5]
        rec = "\n".join(
            f"{medals[i]} <@{s.user_id}> — {s.active} ✅ / {s.total} 👥" for i, s in enumerate(stats) if s.total
        ) or f"{t('ranking_empty', 'en')}"

        embed = discord.Embed(title=f"{t('ranking_title', 'en')}", colour=0xC9A227,
                              timestamp=discord.utils.utcnow())
        embed.add_field(name=f"{t('ranking_participation', 'en')}",
                        value=part, inline=False)
        embed.add_field(name=f"{t('ranking_recruiters', 'en')}",
                        value=rec, inline=False)
        embed.set_footer(text=f"{t('ranking_footer', 'en')} · {cfg.window_days}d")
        return embed

    async def update_ranking_message(self) -> None:
        db = self.bot.db
        channel = await self.bot.channel("ranking")
        if not channel:
            return
        embed = await self.ranking_embed()
        mid = await db.get_int_setting("msg_ranking")
        if mid:
            try:
                await channel.get_partial_message(mid).edit(embed=embed)
                return
            except discord.HTTPException:
                pass
        try:
            msg = await channel.send(embed=embed)
            await db.set_setting("msg_ranking", msg.id)
        except discord.HTTPException:
            pass

    # ================= /profile =================
    @app_commands.command(name="profile", description="See participation and profile")
    @app_commands.describe(member="Member (optional)")
    @app_commands.guild_only()
    async def profile_cmd(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        lang = lang_of(interaction.locale)
        target = member or interaction.user
        db, cfg = self.bot.db, self.bot.cfg
        m = await db.get_member(target.id)
        if not m or not m["verified_at"]:
            await interaction.response.send_message(t("profile_not_member", lang), ephemeral=True)
            return
        s = await db.fetchone("SELECT * FROM scores WHERE user_id=?", (target.id,))
        level, given, received, ratio = (s["level"], s["given"], s["received"], s["ratio"]) if s else (1, 0, 0, 0.0)
        handles = await db.handles_of(target.id)
        genres = [r["genre"] for r in await db.fetchall("SELECT genre FROM member_genres WHERE user_id=?",
                                                        (target.id,))]
        stats = (await recruiter_stats(db, cfg.min_activity)).get(target.id)

        embed = discord.Embed(title=target.display_name, colour=level_color(level))
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name=t("profile_level", lang), value=f"**Lv.{level}**  `{progress_bar(level)}`", inline=False)
        embed.add_field(name=t("profile_given", lang), value=str(given))
        embed.add_field(name=t("profile_received", lang), value=str(received))
        embed.add_field(name=t("profile_ratio", lang), value=f"{ratio:.2f}")
        if genres:
            embed.add_field(name=t("profile_genres", lang), value=" · ".join(genres), inline=False)
        profiles = "\n".join(f"{'✅' if h['verified'] else '⏳'} {PLATFORM_NAME[h['platform']]}: @{h['handle']}"
                             for h in handles)
        if profiles:
            embed.add_field(name=t("profile_profiles", lang), value=profiles, inline=False)
        if stats and stats.total:
            embed.add_field(name=t("profile_recruits", lang), value=f"{stats.active} / {stats.total}")
        embed.set_footer(text=t("profile_footer", lang, days=cfg.window_days, min=cfg.min_activity))
        content = t("paused", lang) if await self.bot.is_paused() else None
        await interaction.response.send_message(content=content, embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Participation(bot))
