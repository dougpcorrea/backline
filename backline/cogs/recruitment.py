"""Recrutamento: link de convite pessoal e registro de quem entrou por qual link.

O Discord não diz diretamente qual convite a pessoa usou. O bot guarda o número
de usos de cada convite e, quando alguém entra, vê qual contador subiu.
"""
from __future__ import annotations

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from ..db import now
from ..i18n import lang_of, t


class Recruitment(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.uses: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def refresh_cache(self) -> list[discord.Invite]:
        guild = self.bot.guild
        if not guild:
            return []
        try:
            invites = await guild.invites()
        except discord.HTTPException:
            await self.bot.log_admin("⚠️ Sem permissão 'Gerenciar servidor' para ler convites; recrutamento não será registrado.")
            return []
        self.uses = {i.code: i.uses or 0 for i in invites}
        return invites

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        async with self._lock:
            await self.refresh_cache()

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        self.uses[invite.code] = invite.uses or 0

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        self.uses.pop(invite.code, None)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot or (self.bot.guild and member.guild.id != self.bot.guild.id):
            return
        db = self.bot.db
        async with self._lock:
            before = dict(self.uses)
            invites = await self.refresh_cache()
        used = [i.code for i in invites if (i.uses or 0) > before.get(i.code, 0)]
        await db.execute("UPDATE members SET left_at=NULL WHERE user_id=?", (member.id,))
        await db.execute("UPDATE recruits SET left_at=NULL WHERE user_id=?", (member.id,))
        if len(used) != 1:
            return  # nenhum ou mais de um convite mudou: não dá pra saber com certeza
        row = await db.fetchone("SELECT owner_id FROM invites WHERE code=?", (used[0],))
        if not row or row["owner_id"] == member.id:
            return
        await db.execute("UPDATE invites SET uses=uses+1 WHERE code=?", (used[0],))
        # Quem recrutou primeiro fica com o crédito (mesmo se a pessoa sair e voltar)
        await db.execute(
            "INSERT INTO recruits(user_id, inviter_id, code, joined_at) VALUES(?,?,?,?) ON CONFLICT DO NOTHING",
            (member.id, row["owner_id"], used[0], now()))

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        db = self.bot.db
        await db.execute("UPDATE members SET left_at=? WHERE user_id=?", (now(), member.id))
        await db.execute("UPDATE recruits SET left_at=? WHERE user_id=?", (now(), member.id))

    @app_commands.command(name="invite", description="Get your personal invite link")
    @app_commands.guild_only()
    async def invite_cmd(self, interaction: discord.Interaction) -> None:
        lang = lang_of(interaction.locale)
        db = self.bot.db
        member = await db.get_member(interaction.user.id)
        if not member or not member["verified_at"]:
            await interaction.response.send_message(
                t("err_not_verified", lang, channel=await self.bot.channel_mention("start")), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        # Reaproveita o convite se ele ainda existir
        rows = await db.fetchall("SELECT code FROM invites WHERE owner_id=?", (interaction.user.id,))
        if rows and interaction.guild:
            live = {i.code for i in await self.refresh_cache()}
            for r in rows:
                if r["code"] in live:
                    await interaction.followup.send(
                        t("invite_ok", lang, url=f"https://discord.gg/{r['code']}"), ephemeral=True)
                    return

        channel = await self.bot.channel("start")
        if not channel:
            await interaction.followup.send(t("invite_error", lang), ephemeral=True)
            return
        try:
            invite = await channel.create_invite(max_age=0, max_uses=0, unique=True,
                                                 reason=f"Backline recruiter {interaction.user.id}")
        except discord.HTTPException:
            await interaction.followup.send(t("invite_error", lang), ephemeral=True)
            return
        await db.execute("INSERT INTO invites(code, owner_id, uses, created_at) VALUES(?,?,0,?) "
                         "ON CONFLICT(code) DO UPDATE SET owner_id=excluded.owner_id",
                         (invite.code, interaction.user.id, now()))
        self.uses[invite.code] = 0
        await interaction.followup.send(t("invite_ok", lang, url=invite.url), ephemeral=True)


async def setup(bot):
    await bot.add_cog(Recruitment(bot))
