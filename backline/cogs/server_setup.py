"""/setup — cria (ou reaproveita) cargos, canais e painéis do servidor.

Pode ser rodado de novo a qualquer momento: o que já existe é reaproveitado.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..i18n import bi, t
from ..scoring import level_color, level_role_name
from ..welcome import build as build_welcome
from .community import SuggestionsView, suggestions_embed
from .feed import PostPanelView
from .mutual import Mutual, MutualPanelView
from .onboarding import JoinView
from .ratings import PANEL_TEXT as RATINGS_TEXT, RatingsPanel

MEMBER_ROLE = "Backline Member"
RECRUITER_ROLE = "🛡️ Recruiter"
FOUNDER_ROLE = "🌟 Founder Member"
FOUNDER_LIMIT = 100   # os primeiros 100 membros verificados viram fundadores
MUTUAL_ROLE = "🤝 Mutual Follow"
CATEGORY = "🎸 BACKLINE"
SETUP_VERSION = "10"  # suba quando mudar canais/cargos/painéis: o bot reaplica sozinho ao reiniciar
COMMUNITY_CATEGORY = "💬 COMMUNITY"
VOICE_CATEGORY = "🎙️ VOICE"
COLLAB_FORUM = "🎼・collabs"
COLLAB_TAGS = ["Vocals", "Production", "Instrumental", "Songwriting", "Mixing / Mastering", "Video / Visuals",
               "Remix", "Feature"]
COLLAB_GUIDE = ("Propose a collaboration: one post per idea. Say what you're making, what you need "
                "(vocals, production, mixing…), your genre, links to your work and how you'd like to split credits.")
DISCOVER_VOICE = "🎧 Discover"
RULES_TEXT = (
    "**1. Be genuine.** Real listens, real comments. No spam, no copy-paste comments, no fake engagement.\n\n"
    "**2. Give before you ask.** Support other artists' work — that's what keeps everyone growing.\n\n"
    "**3. Respect everyone.** No harassment, hate speech or discrimination of any kind.\n\n"
    "**4. Share your own work only.** Post music you created or performed, through the post channel.\n\n"
    "**5. No self-promo outside the system.** No DMs advertising, no links dropped in chats.\n\n"
    "**6. Keep it safe for all.** No NSFW content, scams or suspicious links.\n\n"
    "-# Breaking the rules can lead to losing your level, your access, or a ban. "
    "Questions? Open a private ticket in suggestions.")
VOICE_ROOMS = ["🎙️ Room 1", "🎙️ Room 2", "🎙️ Room 3"]

# chave, nome, visível para (everyone | member | recruiter | admin), membros podem escrever?, categoria
CHANNELS = [
    # o Discord mostra o bloco "brand new server / missões" no 1º canal da lista: o #rules absorve isso
    ("rules", "📌・rules", "everyone", False, "main"),
    ("start", "👋・start-here", "everyone", False, "main"),
    ("post", "📤・post-here", "member", False, "main"),
    ("discover", "🧭・discover", "member", False, "main"),
    ("ratings", "⭐・artist-ratings", "member", False, "main"),
    ("ranking", "🏆・ranking", "member", False, "main"),
    ("mutual", "🤝・mutual-follow", "member", False, "main"),
    ("recruiters", "🛡️・recruiters-lounge", "recruiter", True, "main"),
    ("admin", "🔒・backline-admin", "admin", False, "main"),
    ("general", "💬・general", "member", True, "community"),
    ("suggestions", "💡・suggestions", "member", False, "community"),
]


def genre_role_name(genre: str) -> str:
    return f"🎵 {genre}"


class ServerSetup(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _role(self, guild: discord.Guild, key: str, name: str, color: int = 0,
                    hoist: bool = False) -> discord.Role:
        db = self.bot.db
        rid = await db.get_int_setting(f"role_{key}")
        role = guild.get_role(rid) if rid else None
        role = role or discord.utils.get(guild.roles, name=name)
        if role is None:
            role = await guild.create_role(name=name, colour=discord.Colour(color), hoist=hoist,
                                           mentionable=False, reason="Backline setup")
        elif role.name != name or role.colour.value != color or role.hoist != hoist:
            try:
                await role.edit(name=name, colour=discord.Colour(color), hoist=hoist, reason="Backline setup")
            except discord.HTTPException:
                pass
        await db.set_setting(f"role_{key}", role.id)
        return role

    async def _backfill_founders(self, guild: discord.Guild, founder: discord.Role) -> None:
        """Dono do servidor + membros reais já verificados (por ordem de verificação) até o limite."""
        rows = await self.bot.db.fetchall(
            "SELECT user_id FROM members WHERE verified_at IS NOT NULL AND left_at IS NULL "
            "AND COALESCE(is_test,0)=0 AND demo_name IS NULL ORDER BY verified_at")
        ids = [guild.owner_id] + [int(r["user_id"]) for r in rows]
        given = []
        for uid in dict.fromkeys(ids):
            if len(founder.members) >= FOUNDER_LIMIT:
                break
            m = guild.get_member(uid)
            if m and not m.bot and founder not in m.roles:
                try:
                    await m.add_roles(founder, reason="Backline: Founder Member")
                    given.append(m.mention)
                except discord.HTTPException:
                    pass
        if given:
            await self.bot.log_admin(f"🌟 Founder Member dado para: {', '.join(given)}")

    async def _channel(self, guild, category, key, name, visibility, can_write,
                       member_role, recruiter_role) -> discord.TextChannel:
        db = self.bot.db
        me = guild.me
        ow: dict = {me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True,
                                                    manage_messages=True, read_message_history=True,
                                                    create_instant_invite=True)}
        everyone = guild.default_role
        if visibility == "everyone":
            ow[everyone] = discord.PermissionOverwrite(view_channel=True, send_messages=False,
                                                       add_reactions=False)
        else:
            ow[everyone] = discord.PermissionOverwrite(view_channel=False)
            target = {"member": member_role, "recruiter": recruiter_role}.get(visibility)
            if target:
                ow[target] = discord.PermissionOverwrite(view_channel=True, send_messages=can_write,
                                                         read_message_history=True)
                if key == "suggestions":   # tickets são threads privadas: o membro escreve só dentro delas
                    ow[target].update(send_messages_in_threads=True, create_public_threads=False,
                                      create_private_threads=False)
                    ow[me].update(create_private_threads=True, send_messages_in_threads=True,
                                  manage_threads=True)
        cid = await db.get_int_setting(f"ch_{key}")
        ch = guild.get_channel(cid) if cid else None
        ch = ch or discord.utils.get(guild.text_channels, name=name)
        if ch is None:
            ch = await guild.create_text_channel(name, category=category, overwrites=ow, reason="Backline setup")
        else:
            await ch.edit(category=category, overwrites=ow, reason="Backline setup")
        await db.set_setting(f"ch_{key}", ch.id)
        return ch

    async def refresh_welcome(self, guild: discord.Guild) -> None:
        """Redesenha o painel do #start-here com o texto salvo (/admin welcome) ou o padrão."""
        chans = {k: await self.bot.channel(k) for k in ("start", "post", "discover", "mutual", "ratings")}
        if not chans["start"]:
            return
        jump = lambda ch: f"https://discord.com/channels/{guild.id}/{ch.id}"  # noqa: E731
        mentions = {k: c.mention for k, c in chans.items() if c and k != "start"}
        embed = build_welcome(mentions, await self.bot.db.get_setting("welcome_text"))
        links = [(label, emoji, jump(chans[k])) for label, emoji, k in
                 (("Discover", "🧭", "discover"), ("Post your music", "📤", "post")) if chans[k]]
        await self._panel("join", chans["start"], embed, JoinView(self.bot, links))

    async def _collab_forum(self, guild, category, member_role):
        """Fórum de propostas de colaboração (cada proposta vira um post com tags)."""
        db = self.bot.db
        ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False),
              member_role: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                       send_messages_in_threads=True, read_message_history=True,
                                                       attach_files=True, embed_links=True),
              guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_threads=True)}
        fid = await db.get_int_setting("ch_collabs")
        forum = guild.get_channel(fid) if fid else None
        forum = forum or discord.utils.get(guild.forums, name=COLLAB_FORUM)
        try:
            if forum is None:
                forum = await guild.create_forum(COLLAB_FORUM, category=category, overwrites=ow, topic=COLLAB_GUIDE,
                                                 available_tags=[discord.ForumTag(name=n) for n in COLLAB_TAGS],
                                                 reason="Backline setup")
            else:
                await forum.edit(category=category, overwrites=ow, topic=COLLAB_GUIDE, reason="Backline setup")
        except discord.HTTPException:
            return None   # fóruns exigem servidor "Community" em alguns casos: segue sem ele
        await db.set_setting("ch_collabs", forum.id)
        return forum

    async def _panel(self, key: str, channel: discord.TextChannel, embed: discord.Embed,
                     view: discord.ui.View) -> None:
        db = self.bot.db
        mid = await db.get_int_setting(f"msg_{key}")
        if mid:
            try:
                msg = await channel.fetch_message(mid)
                await msg.edit(embed=embed, view=view)
                return
            except discord.HTTPException:
                pass
        msg = await channel.send(embed=embed, view=view)
        await db.set_setting(f"msg_{key}", msg.id)

    async def _migrate_genres(self, guild: discord.Guild) -> None:
        """Gêneros que saíram da lista: membros passam pro gênero novo e o cargo antigo é apagado."""
        from ..config import LEGACY_GENRES
        db = self.bot.db
        rows = await db.fetchall("SELECT key, value FROM settings WHERE key LIKE 'role_genre_%'")
        for r in rows:
            old = r["key"][len("role_genre_"):]
            if old in self.bot.cfg.genres:
                continue
            new = LEGACY_GENRES.get(old, "Other")
            new_role = await self.bot.role(f"genre_{new}")
            old_role = guild.get_role(int(r["value"])) if r["value"] and r["value"].isdigit() else None
            await db.execute("INSERT INTO member_genres(user_id, genre) SELECT user_id, ? FROM member_genres "
                             "WHERE genre=? ON CONFLICT DO NOTHING", (new, old))
            await db.execute("DELETE FROM member_genres WHERE genre=?", (old,))
            if old_role:
                for m in list(old_role.members):
                    if new_role and new_role not in m.roles:
                        try:
                            await m.add_roles(new_role, reason="Backline genre migration")
                        except discord.HTTPException:
                            pass
                try:
                    await old_role.delete(reason="Backline genre list simplified")
                except discord.HTTPException:
                    pass
            await db.execute("DELETE FROM settings WHERE key=?", (r["key"],))

    @app_commands.command(name="setup", description="Create/refresh Backline channels, roles and panels")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def setup_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        assert interaction.guild
        try:
            chans, forum, role_warning = await self.run_setup(interaction.guild)
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Faltou permissão. O bot precisa de: Gerenciar cargos, Gerenciar canais, "
                "Gerenciar servidor e Criar convites (ou Administrador).", ephemeral=True)
            return
        await interaction.followup.send(
            "✅ Servidor configurado.\n" + "\n".join(f"• {c.mention}" for c in [*chans.values(), forum] if c)
            + role_warning, ephemeral=True)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Depois de uma atualização, aplica a estrutura nova do servidor sozinho (sem precisar do /setup)."""
        if await self.bot.db.get_setting("setup_version") == SETUP_VERSION or not self.bot.cfg.guild_id:
            return
        guild = self.bot.get_guild(self.bot.cfg.guild_id)
        if not guild:
            return
        try:
            await self.run_setup(guild)
            await self.bot.db.set_setting("setup_version", SETUP_VERSION)
            await self.bot.log_admin(f"🛠️ Estrutura do servidor atualizada automaticamente (v{SETUP_VERSION}).")
        except discord.HTTPException as e:
            await self.bot.log_admin(f"⚠️ Setup automático falhou: `{e}` — rode /setup.")

    async def run_setup(self, guild: discord.Guild):
        cfg = self.bot.cfg
        if True:
            # ---- cargos (criados de baixo pra cima) ----
            genre_roles = [await self._role(guild, f"genre_{g}", genre_role_name(g)) for g in cfg.genres]
            await self._migrate_genres(guild)
            member_role = await self._role(guild, "member", MEMBER_ROLE)
            recruiter_role = await self._role(guild, "recruiter", RECRUITER_ROLE, 0xC9A227)
            await self._role(guild, "mutual", MUTUAL_ROLE)
            founder_role = await self._role(guild, "founder", FOUNDER_ROLE, 0xE8B04B)
            level_roles = [await self._role(guild, f"level_{n}", level_role_name(n), level_color(n), hoist=False)
                           for n in range(1, 11)]

            # Ordem: níveis no topo (a cor do nome vem deles), depois divulgador, membro, gêneros.
            ordered = list(reversed(level_roles)) + [founder_role, recruiter_role, member_role] + genre_roles
            top = guild.me.top_role.position
            positions = {}
            pos = top - 1
            for role in ordered:
                if pos < 1:
                    break
                positions[role] = pos
                pos -= 1
            role_warning = ""
            try:
                await guild.edit_role_positions(positions=positions, reason="Backline setup")
            except discord.HTTPException:
                role_warning = ("\n⚠️ Não consegui reordenar os cargos. Arraste o cargo do bot para o topo "
                                "da lista em Configurações do servidor > Cargos e rode /setup de novo.")

            await self._backfill_founders(guild, founder_role)

            # ---- canais ----
            # o #feed saiu: o Discover substitui a linha do tempo
            old_feed = await self.bot.db.get_int_setting("ch_feed")
            if old_feed and guild.get_channel(old_feed):
                await guild.get_channel(old_feed).delete(reason="Backline: feed replaced by Discover")
            await self.bot.db.execute("DELETE FROM settings WHERE key='ch_feed'")
            cats = {}
            for ckey, cname in (("main", CATEGORY), ("community", COMMUNITY_CATEGORY)):
                cats[ckey] = discord.utils.get(guild.categories, name=cname) or \
                    await guild.create_category(cname, reason="Backline setup")
            chans = {}
            for key, name, vis, write, ckey in CHANNELS:
                chans[key] = await self._channel(guild, cats[ckey], key, name, vis, write,
                                                 member_role, recruiter_role)
            forum = await self._collab_forum(guild, cats["community"], member_role)

            # ---- canais de voz (só membros) ----
            vcat = discord.utils.get(guild.categories, name=VOICE_CATEGORY) or \
                await guild.create_category(VOICE_CATEGORY, reason="Backline setup")
            vow = {guild.default_role: discord.PermissionOverwrite(view_channel=False),
                   member_role: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True,
                                                            stream=True, use_voice_activation=True),
                   guild.me: discord.PermissionOverwrite(view_channel=True, connect=True)}
            await vcat.edit(overwrites=vow, reason="Backline setup")
            for i, name in enumerate(VOICE_ROOMS, start=1):
                vid = await self.bot.db.get_int_setting(f"vc_{i}")
                vc = guild.get_channel(vid) if vid else None
                vc = vc or discord.utils.get(guild.voice_channels, name=name)
                if vc is None:
                    vc = await guild.create_voice_channel(name, category=vcat, overwrites=vow, reason="Backline setup")
                else:
                    await vc.edit(name=name, category=vcat, overwrites=vow, reason="Backline setup")
                await self.bot.db.set_setting(f"vc_{i}", vc.id)

            # o canal de voz do Discover foi testado e removido
            did = await self.bot.db.get_int_setting("vc_discover")
            if did and guild.get_channel(did):
                await guild.get_channel(did).delete(reason="Backline: voice Discover removed")
            await self.bot.db.execute("DELETE FROM settings WHERE key='vc_discover'")

            # O Discord mostra o card "Welcome to <server> / brand new, shiny server" no
            # canal de mensagens do sistema. Manda ele (e os avisos de entrada) pro canal admin.
            if guild.system_channel != chans["admin"]:
                try:
                    await guild.edit(system_channel=chans["admin"], reason="Backline setup")
                except discord.HTTPException:
                    pass

            # ---- painéis ----
            # ordem fixa dentro de cada categoria (o #rules sempre no topo)
            for pos, (key, *_rest) in enumerate(CHANNELS):
                try:
                    if chans[key].position != pos:
                        await chans[key].edit(position=pos, reason="Backline setup")
                except discord.HTTPException:
                    pass
            rules = discord.Embed(title="📌 Backline Rules", colour=0x30A46C, description=RULES_TEXT)
            await self._panel("rules", chans["rules"], rules, discord.ui.View())
            await self.refresh_welcome(guild)
            await self._panel("suggestions", chans["suggestions"], suggestions_embed(), SuggestionsView(self.bot))
            post = discord.Embed(title=t('post_panel_title', 'en'),
                                 description=bi("post_panel_desc", n=cfg.posts_per_week), colour=0x5865F2)
            await self._panel("post", chans["post"], post, PostPanelView(self.bot))
            await self._panel("mutual", chans["mutual"], Mutual.panel_embed(), MutualPanelView(self.bot))
            await self._panel("ratings", chans["ratings"],
                              discord.Embed(description=RATINGS_TEXT, colour=0xF0B232), RatingsPanel(self.bot))

            part = self.bot.get_cog("Participation")
            if part:
                await part.update_ranking_message()
        return chans, forum, role_warning


async def setup(bot):
    await bot.add_cog(ServerSetup(bot))
